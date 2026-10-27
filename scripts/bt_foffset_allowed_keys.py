"""The f-offset bench's allowed-keys file: what of an aligner's ``NAME.align.json`` the operator sees before the stage-1 freeze.

    python scripts/bt_foffset_allowed_keys.py NAME.align.json NAME.align_allowed.json

``bt_bench_session.py close --allowed-keys`` runs it in ``python -I``. The same view as the confirmatory's
``s4_bench_allowed_keys.py``: whether the aligner accepted, the names and the clock line (``ppm``, ``delay_at_0``).
The carrier values (``f_located_hz``, ``cfo_instrument_hz``, ``cfo_instrument_spread_hz``, ``reference_error_hz``),
the residuals, the refusal reason and the commanded offset are dropped by construction: only ``ALLOWED`` is copied.
The session also refuses a view holding any carrier key, whatever this returns. Standard library only.
Tests: ``scripts/test_bt_foffset_decide.py``.
"""
import json
import sys

ALLOWED = ('accepted', 'shot', 'capture', 'capture_samples', 'ppm', 'delay_at_0')


def allowed_view(align):
    """The allowed keys present (a refusal stops the aligner early, so later keys are absent) and ``accepted``, true only
    when ``problem`` is there and None."""
    out = {k: align[k] for k in ALLOWED if k in align and k != 'accepted'}
    out['accepted'] = 'problem' in align and align['problem'] is None
    return out


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    with open(dst, 'w') as f:
        json.dump(allowed_view(json.load(open(src))), f, indent=1)
