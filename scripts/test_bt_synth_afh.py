#!/usr/bin/env python3
"""Hold the narrow-AFH train to the hop kernel, and the files to the sidecar.

    python scripts/test_bt_synth_afh.py

``scripts/bt_synth_hop.py`` puts a Basic Rate train on the channels of
``apps/bt_hop.py``'s adapted sequence, under a map of 20 adjacent channels that
may change part way, and writes it as a cf32 and a sidecar for
bluey-ox-walker. ``scripts/test_bt_synth_hop.py`` holds the modulator to what
it was told to put where; this holds what it was told to the kernel:

* every burst's channel, worked out here from the clock, the address and the
  planted map by calling ``bt_hop.hop_channel`` and not by calling
  ``afh_hop_fn`` back, for a DH5, a DH3 and a DH1 train;
* a map change: the bursts before it in map A, from it in map B, with channels
  only B has appearing after and only A has appearing before, and the sidecar
  carrying both instants and the right map index on every burst;
* the sidecar's AFH keys, and that it survives a round trip through JSON;
* ``afh_hop_fn`` and ``synthesise_afh`` refusing what makes no sense;
* the command line, in a subprocess and then in process, writing a pair of
  files that are the same bytes the second time, with every call given
  ``--out``, since the default folders belong to another project;
* what a reviewer found: a file with a map change says in its sidecar that
  ``afh_map`` is only the first map, a map naming a channel the tuning cannot
  hold is refused even when the sequence would not land on it, and
  ``generator_commit`` does not call an unchecked tree clean;
* ``STAGE3_SET``: six names, each map inside what the tuning holds, checked
  with ``hop_plan`` and no samples, and the sequence of a map really hopping.

The files go in a temporary folder and nothing is transmitted. The big set
itself is not written here - a gigabyte - but a small one registered for the
test goes through the same ``--set`` code.
"""
import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_hop as afh  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bt_synth_hop.py')
failures = []

CLK0 = 0x0123400
LAP = 0x9E8B33
UAP = 0x47
MAP_A = list(range(33, 53))                      # 20 channels, 2435 to 2454 MHz
MAP_B = list(range(36, 56))                      # 20 channels, 2438 to 2457 MHz


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def mask(channels):
    return sum(1 << c for c in channels)


def period(ptype):
    """Master slots from one burst's first slot to the next's."""
    return br.PACKET_TYPES[ptype][1] + 1


def expected(ptype, bursts, maps, lap=LAP, uap=UAP, clk0=CLK0):
    """The truth, worked out here: for burst k the clock ``clk0 + 2 * period *
    k``, the map of the last ``(first_burst, channels)`` not after k, and the
    kernel's answer. Returns ``[(map index, channel)]``."""
    out = []
    for k in range(bursts):
        index = max(i for i, (first, _) in enumerate(maps) if first <= k)
        clk = clk0 + 2 * period(ptype) * k
        out.append((index, bt_hop.hop_channel(clk, uap << 24 | lap, mask(maps[index][1]))))
    return out


def raises(call):
    """The message of the ``ValueError`` a call raises, or None if it does not."""
    try:
        call()
    except ValueError as e:
        return str(e)
    return None


def check_kernel(ptype, bursts, lap=LAP, uap=UAP, clk0=CLK0, note=''):
    iq, side = afh.synthesise_afh([(0, MAP_A)], ptype, bursts=bursts, lap=lap, uap=uap,
                                  clk0=clk0, seed=4)
    want = expected(ptype, bursts, [(0, MAP_A)], lap, uap, clk0)
    got = [e['channel'] for e in side['bursts']]
    check(got == [c for _, c in want],
          '%s%s: all %d channels are bt_hop.hop_channel of the clock under the map'
          % (ptype, note, bursts))
    check(set(got) <= set(MAP_A) and side['hop_channels'] == sorted(set(got)),
          '%s: every burst is inside the 20 channels of the map, %d of them used'
          % (ptype, len(set(got))))
    check([e['clk'] for e in side['bursts']] == [clk0 + 2 * period(ptype) * k for k in range(bursts)],
          '%s: the clock advances %d ticks a burst' % (ptype, 2 * period(ptype)))
    check(len(set(got)) >= 8, '%s: %d different channels in %d bursts, the sequence moves'
          % (ptype, len(set(got)), bursts))
    return iq, side


def check_change():
    print('\nA map change under the train')
    bursts, at, ptype = 120, 60, 'DH1'
    iq, side = afh.synthesise_afh([(0, MAP_A), (at, MAP_B)], ptype, bursts=bursts, seed=7)
    want = expected(ptype, bursts, [(0, MAP_A), (at, MAP_B)])
    got = [e['channel'] for e in side['bursts']]
    check(got == [c for _, c in want], 'every channel is the kernel\'s under the map in force')
    before, after = set(got[:at]), set(got[at:])
    check(before <= set(MAP_A) and after <= set(MAP_B),
          'bursts 0-%d are inside map A and %d-%d inside map B' % (at - 1, at, bursts - 1))
    only_a, only_b = set(MAP_A) - set(MAP_B), set(MAP_B) - set(MAP_A)
    check(after & only_b and not before & only_b,
          'channels %s only B has: %d of them appear after the change, none before'
          % (sorted(only_b), len(after & only_b)))
    check(before & only_a and not after & only_a,
          'channels %s only A has: %d of them appear before the change, none after'
          % (sorted(only_a), len(before & only_a)))
    clk_at = CLK0 + 2 * period(ptype) * at
    inst = [m['instant'] for m in side['afh_maps']]
    check(inst == [CLK0 >> 1, clk_at >> 1] and side['afh_instant'] == CLK0 >> 1,
          'afh_maps carries both instants, %#x and %#x, and afh_instant is the first'
          % (inst[0], inst[1]))
    check([m['first_burst'] for m in side['afh_maps']] == [0, at]
          and [m['channels'] for m in side['afh_maps']] == [MAP_A, MAP_B],
          'afh_maps names the burst each starts on and its sorted channels')
    check([e['afh_map_index'] for e in side['bursts']] == [i for i, _ in want]
          and [e['afh_map_index'] for e in side['bursts']] == [0] * at + [1] * (bursts - at),
          'afh_map_index is 0 for the first %d bursts and 1 for the last %d' % (at, bursts - at))
    return side


def check_instants():
    print('\nWhich map a clock is under')
    fn = afh.afh_hop_fn([(100, MAP_A), (200, MAP_B)], LAP, UAP)
    address = UAP << 24 | LAP
    cases = [(200, MAP_A), (398, MAP_A), (399, MAP_A), (400, MAP_B), (401, MAP_B), (4000, MAP_B)]
    check(all(fn(clk) == bt_hop.hop_channel(clk, address, mask(m)) for clk, m in cases),
          'a clock uses the last map whose instant is not after its CLK[27:1]: 200 and 398-399 '
          'are CLK[27:1] 100-199, map A, and 400 on is map B')
    # A change of map that shows: the same clock gives other channels under the other map.
    check(any(fn(clk) != bt_hop.hop_channel(clk, address, mask(MAP_B)) for clk in range(200, 400, 4)),
          'and map A is not map B: the two give different channels below the instant')


def check_sidecar(side):
    print('\nThe sidecar')
    keys = ['afh_map', 'afh_instant', 'afh_maps', 'afh_instant_meaning', 'hop_kernel',
            'address_for_hop', 'clock_lock_note']
    check(all(k in side for k in keys), 'the top level has all %d AFH keys' % len(keys))
    check(side['afh_map'] == sorted(MAP_A) and side['afh_instant'] == CLK0 >> 1
          and side['afh_instant'] == side['afh_maps'][0]['instant'],
          'afh_map is the sorted first map, afh_instant its CLK[27:1] instant, %#x' % side['afh_instant'])
    check(side['address_for_hop'] == UAP << 24 | LAP,
          'address_for_hop is uap << 24 | lap, %#x' % side['address_for_hop'])
    check('every burst of the file is on an adapted sequence' in side['afh_instant_meaning']
          and 'CLK[27:1]' in side['afh_instant_meaning'],
          'afh_instant_meaning says the first map applies from the first burst')
    check('minus 1' in side['clock_lock_note'] and 'Part G' in side['clock_lock_note']
          and 'no shift' in side['clock_lock_note'], 'clock_lock_note is there and says what bluey returns')
    check('bt_hop.py' in side['hop_kernel'] and 'Part G' in side['hop_kernel'],
          'hop_kernel names the kernel and what it was checked against')
    check(all(type(e['afh_map_index']) is int for e in side['bursts']),
          'every burst has an integer afh_map_index')
    check(side['hopping'] is True and 'hop_channels' in side and side['channel_mhz'] is None,
          'it is still synthesise_hop\'s sidecar: hopping, hop_channels, no one channel_mhz')
    check(list(side)[-1] == 'bursts', 'the burst list is last')
    check(json.loads(json.dumps(side)) == side, 'the whole sidecar is plain JSON, and survives a round trip')


def check_refusals():
    print('\nWhat is refused')
    fn = afh.afh_hop_fn([(1000, MAP_A)], LAP, UAP)
    why = raises(lambda: fn(1998))
    check(why is not None and '0x3e8' in why, 'a first instant after the clock asked raises: %s'
          % (why or 'it did not')[:70])
    check(raises(lambda: fn(2000)) is None and raises(lambda: fn(2001)) is None,
          'a clock at or after the first instant does not')
    for what, maps in (('a channel above 78', [(0, [33, 79])]), ('a negative channel', [(0, [-1, 33])]),
                       ('a channel that is not a number', [(0, ['a'])]), ('an empty map', [(0, [])]),
                       ('no maps', []), ('instants that do not increase', [(0, MAP_A), (0, MAP_B)]),
                       ('instants running backwards', [(50, MAP_A), (20, MAP_B)])):
        why = raises(lambda: afh.afh_hop_fn(maps, LAP, UAP)(100))
        check(why is not None, '%s raises ValueError: %s' % (what, (why or 'it did not')[:60]))
    for what, call in (
            ('a first map that is not from burst 0', lambda: afh.synthesise_afh([(1, MAP_A)], bursts=4)),
            ('a map that starts after the last burst',
             lambda: afh.synthesise_afh([(0, MAP_A), (4, MAP_B)], bursts=4)),
            ('first bursts that do not increase',
             lambda: afh.synthesise_afh([(0, MAP_A), (3, MAP_B), (2, MAP_A)], bursts=6)),
            ('an unknown packet type', lambda: afh.synthesise_afh([(0, MAP_A)], 'DH7', bursts=4)),
            ('a channel outside 0-78 in a map', lambda: afh.synthesise_afh([(0, [33, 90])], bursts=4))):
        why = raises(call)
        check(why is not None, '%s raises ValueError: %s' % (what, (why or 'it did not')[:60]))
    # A map given as a one-shot iterator is read once, not twice.
    iq, side = afh.synthesise_afh([(0, iter(MAP_A))], 'DH1', bursts=4)
    check(side['afh_map'] == MAP_A, 'a map given as an iterator is still the whole map')


def check_review_fixes():
    print('\nA file with a map change, a map the tuning cannot hold, an unchecked tree')
    _, one = afh.synthesise_afh([(0, MAP_A)], 'DH1', bursts=6)
    _, two = afh.synthesise_afh([(0, MAP_A), (3, MAP_B)], 'DH1', bursts=6)
    check(one['afh_map_count'] == 1 and two['afh_map_count'] == 2,
          'afh_map_count is the number of maps: 1 and 2')
    check(two['afh_map'] == MAP_A and [m['channels'] for m in two['afh_maps']] == [MAP_A, MAP_B],
          'on a change file afh_map is the first map and afh_maps has both')
    meaning = two['afh_instant_meaning']
    check('FIRST' in meaning and 'afh_maps' in meaning and 'afh_map_count' in meaning,
          'afh_instant_meaning says afh_map is the first map only, and where all of them are')
    check('2026-10-04' in two['clock_lock_note'] and 'fix' in two['clock_lock_note'],
          'clock_lock_note says as of when it is true, and that a fixed lock returns the true clock')

    # Channel 0 is 42.5 MHz from the centre. Four DH1 bursts do not land on it,
    # so a refusal that waited for the sequence would let this through.
    plain = [bt_hop.hop_channel((CLK0 + 4 * k) & 0xFFFFFFF, (UAP << 24) | LAP, mask(MAP_A + [0]))
             for k in range(4)]
    why = raises(lambda: afh.synthesise_afh([(0, MAP_A + [0])], 'DH1', bursts=4))
    check(0 not in plain and why is not None and 'channel 0' in why and 'map 0' in why,
          'a map naming channel 0 is refused, though four bursts would not reach it: %s'
          % (why or 'it was not')[:60])
    why = raises(lambda: afh.synthesise_afh([(0, MAP_A), (2, MAP_B + [60])], 'DH1', bursts=4))
    check(why is not None and 'map 1' in why and 'channel 60' in why,
          'and the second map is named when it is the second that holds one: %s' % (why or 'no')[:60])

    # commit(): an empty answer from a failed git status is not a clean tree.
    class Done:
        def __init__(self, out, code=0):
            self.stdout, self.returncode = out, code

    def with_status(out, code):
        real = bt_synth.subprocess.run
        bt_synth.subprocess.run = lambda cmd, **kw: Done('abc1234\n') if 'rev-parse' in cmd else Done(out, code)
        try:
            return bt_synth.commit()
        finally:
            bt_synth.subprocess.run = real
    check(with_status('', 0) == 'abc1234', 'a clean tree is the hash alone')
    check(with_status(' M scripts/x.py\n', 0) == 'abc1234-dirty', 'a changed file is -dirty')
    check(with_status('', 128) == 'abc1234-unchecked', 'a failed git status is -unchecked, not clean')


def check_centre():
    print('\nA centre that is not a whole number of MHz')
    import warnings
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        afh.synthesise_afh([(0, MAP_A)], 'DH1', bursts=2, center_mhz=2444.5)
    check(any('whole number of MHz' in str(w.message) for w in seen),
          '2444.5 MHz warns that a 1 MHz channelizer would see every channel half a bin off')
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        afh.synthesise_afh([(0, MAP_A)], 'DH1', bursts=2)
    check(not seen, 'the default centre, %g MHz, does not' % inspect.signature(
        afh.synthesise_afh).parameters['center_mhz'].default)


def cli(*argv, out):
    """``bt_synth_hop.main`` in this process, ``--out`` always given. Returns
    ``(exit code, stdout)``; a refusal by the parser is exit code 2."""
    text = io.StringIO()
    try:
        with contextlib.redirect_stdout(text), contextlib.redirect_stderr(io.StringIO()):
            afh.main(list(argv) + ['--out', out])
    except SystemExit as e:
        return e.code, text.getvalue()
    return 0, text.getvalue()


def read_pair(folder, name):
    with open(os.path.join(folder, 'synth_%s.cf32' % name), 'rb') as f:
        iq = f.read()
    with open(os.path.join(folder, 'synth_%s.json' % name), 'rb') as f:
        side = f.read()
    return iq, side


def sample_count(side):
    """What the sidecar says the file holds, as the writer lays it out: the
    first burst's ``start_sample``, one period of slots a burst, and a slot to
    finish - worked out from the sidecar's own numbers, not the file's size."""
    slots = period(side['bursts'][0]['ptype'])
    return side['bursts'][0]['start_sample'] + len(side['bursts']) * slots * side['slot_samples'] \
        + side['slot_samples']


def check_command_line(tmp):
    print('\nThe command line')
    one, two = os.path.join(tmp, 'one'), os.path.join(tmp, 'two')
    base = ['--ptype', 'DH5', '--bursts', '8', '--map', '33-52', '--cfo', '5e3',
            '--timing-frac', '0.25', '--snr', '20', '--start-offset', '3']
    args = ['cli_a'] + base + ['--seed', '3001']
    runs = [subprocess.run([sys.executable, '-B', SCRIPT] + args + ['--out', d],
                           capture_output=True, text=True, timeout=300) for d in (one, two)]
    check(all(r.returncode == 0 for r in runs), 'python scripts/bt_synth_hop.py %s exits 0%s'
          % (' '.join(args[:7]), '' if runs[0].returncode == 0 else ': ' + runs[0].stderr[-200:]))
    names = sorted(os.listdir(one)) if os.path.isdir(one) else []
    check(names == ['synth_cli_a.cf32', 'synth_cli_a.json'], 'it writes both files, and only them: %s' % names)
    if names != ['synth_cli_a.cf32', 'synth_cli_a.json']:
        return
    iq1, side1 = read_pair(one, 'cli_a')
    iq2, side2 = read_pair(two, 'cli_a')
    side = json.loads(side1)
    check(side['afh_map'] == MAP_A and len(side['bursts']) == 8 and side['bursts'][0]['ptype'] == 'DH5'
          and side['cfo_hz'] == 5e3 and side['snr_db'] == 20 and side['start_offset'] == 3
          and side['timing_frac'] == 0.25 and side['bursts'][0]['start_sample'] == 62500 + 3,
          'the .json parses, and holds the options given: 8 DH5 bursts, map 33-52, cfo, snr, offset')
    n = sample_count(side)
    check(len(iq1) == n * 8, 'the .cf32 is %d bytes, the sidecar\'s %d samples x 8' % (len(iq1), n))
    check(('%d samples' % n) in runs[0].stdout and os.path.join(one, 'synth_cli_a.cf32') in runs[0].stdout
          and os.path.join(one, 'synth_cli_a.json') in runs[0].stdout and 'ms' in runs[0].stdout
          and '8 DH5 bursts' in runs[0].stdout
          and 'channels used: %s' % afh.channel_text(side['hop_channels']) in runs[0].stdout,
          'it prints both paths, the sample count, the milliseconds, the bursts and the channels used')
    check(iq1 == iq2 and side1 == side2,
          'the same arguments twice give the same bytes, samples and sidecar')
    # The same run, with a different seed, is another recording of the same bursts.
    code, _ = cli('cli_b', *base, '--seed', '5', out=one)
    iq3, side3 = read_pair(one, 'cli_b')
    check(code == 0 and iq3 != iq1 and json.loads(side3)['bursts'] == side['bursts'],
          'another seed is other samples and the same bursts')

    print('\nOptions, and what is refused')
    check(afh.channel_list('33-52') == MAP_A and afh.channel_list('1,5, 9') == [1, 5, 9]
          and afh.channel_list('33-35,40') == [33, 34, 35, 40]
          and afh.channel_text([33, 34, 35, 40, 52]) == '33-35,40,52',
          'a map is a range, a comma list or both, and prints back as ranges')
    sub = os.path.join(tmp, 'opts')
    code, text = cli('cli_c', '--bursts', '8', '--ptype', 'DH1', '--map', '40,42-44',
                     '--map-b', '41-45', '--change-at-burst', '4', '--clk0', '0x1000', out=sub)
    side = json.loads(read_pair(sub, 'cli_c')[1]) if code == 0 else {}
    check(code == 0 and side.get('afh_map') == [40, 42, 43, 44]
          and [m['first_burst'] for m in side['afh_maps']] == [0, 4]
          and side['afh_maps'][1]['channels'] == [41, 42, 43, 44, 45] and side['clk'] == 0x1000
          and [e['afh_map_index'] for e in side['bursts']] == [0, 0, 0, 0, 1, 1, 1, 1],
          '--map 40,42-44 (a comma list, with a range), --map-b with --change-at-burst 4 and --clk0 0x1000')
    refused = [
        ('--map-b without --change-at-burst', ['x', '--map-b', '36-55']),
        ('--change-at-burst without --map-b', ['x', '--change-at-burst', '4']),
        ('no name and no --set', ['--bursts', '8']),
        ('a name with --set', ['x', '--set', 'stage3']),
        ('--set with another option', ['--set', 'stage3', '--bursts', '8']),
        ('an unknown --set', ['--set', 'stage9']),
        ('a backwards range', ['x', '--map', '52-33']),
        ('a map that is not channels', ['x', '--map', 'a-b']),
        ('a channel above 78', ['x', '--map', '70-90']),
        ('a change at a burst that does not exist', ['x', '--bursts', '8', '--map-b', '36-55',
                                                     '--change-at-burst', '8']),
    ]
    for what, argv in refused:
        code, _ = cli(*argv, out=sub)
        check(code not in (0, None), '%s exits non-zero (%s)' % (what, code))
    check(sorted(os.listdir(sub)) == ['synth_cli_c.cf32', 'synth_cli_c.json'],
          'and none of those wrote a file')
    # The real command line, for the one that matters most: an unknown set.
    r = subprocess.run([sys.executable, '-B', SCRIPT, '--set', 'stage9', '--out', os.path.join(tmp, 'none')],
                       capture_output=True, text=True, timeout=300)
    check(r.returncode != 0 and not os.path.exists(os.path.join(tmp, 'none')),
          'python scripts/bt_synth_hop.py --set stage9 exits %d and writes nothing' % r.returncode)


def check_set_code_path(tmp):
    print('\nA set goes through the same code')
    table = [('t_one', dict(ptype='DH1', bursts=6, maps_spec=[(0, MAP_A)], seed=9)),
             ('t_two', dict(ptype='DH3', bursts=6, maps_spec=[(0, MAP_A), (3, MAP_B)], seed=10,
                            cfo_hz=1e3, snr_db=15.0, start_offset=7))]
    afh.SETS['tiny'] = table
    try:
        one, two = os.path.join(tmp, 'set1'), os.path.join(tmp, 'set2')
        codes = [cli('--set', 'tiny', out=d)[0] for d in (one, two)]
        want = ['synth_t_one.cf32', 'synth_t_one.json', 'synth_t_two.cf32', 'synth_t_two.json']
        check(codes == [0, 0] and sorted(os.listdir(one)) == want,
              '--set writes the %d files of its table and nothing else' % len(want))
        same = all(read_pair(one, n)[0] == read_pair(two, n)[0] for n in ('t_one', 't_two'))
        sides = [json.loads(read_pair(one, n)[1]) for n in ('t_one', 't_two')]
        check(same and all(read_pair(one, n)[1] == read_pair(two, n)[1] for n in ('t_one', 't_two')),
              'the same command twice gives byte-identical files')
        check(sides[1]['cfo_hz'] == 1e3 and sides[1]['snr_db'] == 15 and sides[1]['start_offset'] == 7
              and [m['first_burst'] for m in sides[1]['afh_maps']] == [0, 3],
              'each file has the settings of its own row')
        # The one path: a single run with the row's own options is the row's file.
        code, _ = cli('t_one', '--ptype', 'DH1', '--bursts', '6', '--seed', '9', out=os.path.join(tmp, 'set3'))
        check(code == 0 and read_pair(os.path.join(tmp, 'set3'), 't_one')[0] == read_pair(one, 't_one')[0],
              'and a single run with that row\'s options writes the same samples')
    finally:
        del afh.SETS['tiny']


def check_table():
    print('\nSTAGE3_SET')
    names = [n for n, _ in afh.STAGE3_SET]
    want = ['hop20_dh5', 'hop20_dh3', 'hop20_dh1', 'hop20_dh1_long', 'hop20_imp_dh5', 'hop20_change_dh5']
    check(names == want and afh.SETS['stage3'] is afh.STAGE3_SET and sorted(afh.SETS) == ['stage3'],
          'six names, as asked, and the set is called stage3')
    table = dict(afh.STAGE3_SET)
    rows = {'hop20_dh5': ('DH5', 100, 3001), 'hop20_dh3': ('DH3', 120, 3002),
            'hop20_dh1': ('DH1', 400, 3003), 'hop20_dh1_long': ('DH1', 1000, 3004),
            'hop20_imp_dh5': ('DH5', 120, 3005), 'hop20_change_dh5': ('DH5', 120, 3006)}
    check(all((table[n]['ptype'], table[n]['bursts'], table[n]['seed']) == r for n, r in rows.items()),
          'each has its packet type, burst count and seed')
    imp = table['hop20_imp_dh5']
    check((imp['cfo_hz'], imp['timing_frac'], imp['snr_db'], imp['start_offset']) == (12e3, 0.37, 15, 7)
          and all(not {'cfo_hz', 'timing_frac', 'snr_db', 'start_offset'} & set(table[n])
                  for n in want if n != 'hop20_imp_dh5'),
          'hop20_imp_dh5 has cfo 12 kHz, 0.37 of a sample, 15 dB and offset 7; no other row has an impairment')
    chg = table['hop20_change_dh5']['maps_spec']
    check([(f, sorted(c)) for f, c in chg] == [(0, MAP_A), (60, MAP_B)]
          and all([(f, sorted(c)) for f, c in table[n]['maps_spec']] == [(0, MAP_A)]
                  for n in want if n != 'hop20_change_dh5'),
          'every row is map 33-52, but hop20_change_dh5, 33-52 then 36-55 from burst 60')

    params = inspect.signature(afh.synthesise_afh).parameters
    for name, spec in afh.STAGE3_SET:
        fs = spec.get('fs', params['fs'].default)
        center = spec.get('center_mhz', params['center_mhz'].default)
        clk0 = spec.get('clk0', params['clk0'].default)
        lap, uap = spec.get('lap', params['lap'].default), spec.get('uap', params['uap'].default)
        pd = period(spec['ptype'])
        # Every channel of every map, one a burst: the whole map fits, whether or not the
        # sequence reaches it.
        fits = []
        for first, channels in spec['maps_spec']:
            channels = sorted(channels)
            fits.append(raises(lambda: afh.hop_plan(
                lambda clk: channels[(clk - clk0) // (2 * pd)], len(channels), clk0, pd, fs, center)))
        check(all(f is None for f in fits),
              '%s: the %d map%s fit%s the window at %g MS/s on %g MHz%s'
              % (name, len(fits), 's' if len(fits) > 1 else '', '' if len(fits) > 1 else 's',
                 fs / 1e6, center, '' if all(f is None for f in fits) else ': ' + str(fits)))
        # And the run itself, burst by burst, as synthesise_afh would plan it.
        maps = [(((clk0 + 2 * pd * first) & 0x0FFFFFFF) >> 1, channels)
                for first, channels in spec['maps_spec']]
        plan = afh.hop_plan(afh.afh_hop_fn(maps, lap, uap), spec['bursts'], clk0, pd, fs, center)
        want_plan = expected(spec['ptype'], spec['bursts'], spec['maps_spec'], lap, uap, clk0)
        check([c for _, c in plan] == [c for _, c in want_plan],
              '%s: all %d bursts are on the kernel\'s channel' % (name, spec['bursts']))
        if name == 'hop20_change_dh5':
            got = [c for _, c in plan]
            only_b = set(MAP_B) - set(MAP_A)
            check(set(got[:60]) <= set(MAP_A) and set(got[60:]) <= set(MAP_B)
                  and set(got[60:]) & only_b and not set(got[:60]) & only_b,
                  '%s: map A to burst 59, map B from 60, and channels %s, only B\'s, show up '
                  'after the change and not before' % (name, sorted(only_b)))


def check_spread():
    print('\nThe sequence is not stuck')
    pd = period('DH5')
    maps = [(CLK0 >> 1, MAP_A)]
    plan = afh.hop_plan(afh.afh_hop_fn(maps, LAP, UAP), 100, CLK0, pd, 40e6, 2445.0)
    counts = {}
    for _, channel in plan:
        counts[channel] = counts.get(channel, 0) + 1
    share = 100 / len(MAP_A)
    check(set(counts) == set(MAP_A), '100 DH5 bursts hop on all %d channels of the map, %d reached'
          % (len(MAP_A), len(counts)))
    check(max(counts.values()) <= 3 * share,
          'no channel more than 3 x the even share of %.0f: the busiest has %d, the quietest %d'
          % (share, max(counts.values()), min(counts.values())))


def main():
    print('A train on the adapted sequence, 20 channels')
    _, side = check_kernel('DH5', 24)
    check_kernel('DH3', 30)
    check_kernel('DH1', 48)
    check_kernel('DH1', 16, lap=0x123456, uap=0x12, clk0=0x0ABC000, note=' (another address and clock)')
    check_change()
    check_instants()
    check_sidecar(side)
    check_refusals()
    check_review_fixes()
    check_centre()
    with tempfile.TemporaryDirectory() as tmp:
        check_command_line(tmp)
        check_set_code_path(tmp)
    check_table()
    check_spread()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
