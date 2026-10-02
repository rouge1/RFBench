# Media: how the pickers find files, and MP3

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## How the pickers find media

Every list of files in every dialog - the WAV lists in AM Audio, FM Audio,
FM Subcarrier, PPM-OOK, FM + RDS and the two video transmitters' sound, the
clips, the ATSC streams and the `.dat` stills - comes from
`media_files()` in **`apps/media.py`**. They used to list the folder each
in their own way, and disagreed:

- **None of them looked in subfolders**, so a clip filed under
  `media/Prelinger/` was invisible to every app. The walk goes to any
  depth now, and an entry is labelled with its folder -
  `Prelinger/Chevrolet 1955 Heres Looking`. A file at the top keeps exactly
  the label it always had, and its full path is unchanged, which is what
  keeps a choice saved before this pointing at the same file.
- **Five matched `.wav` case-sensitively and the rest did not.** AM Audio,
  FM Audio, FM Subcarrier, PPM-OOK and FM + RDS used `glob('*.wav')`, which
  on Linux misses `SONG.WAV`, while the NTSC and FM video transmitters
  lower-cased the name first. So one file could be offered by one
  transmitter and not the next - on Linux only, since Windows filenames
  are not case-sensitive at all. Every extension now matches whatever its
  case.
- **Two did not sort**, and came out in whatever order the filesystem
  returned. Everything is top-level files first, then each subfolder's
  grouped, alphabetical ignoring case.

Things it skips on purpose: hidden folders and files - macOS leaves a
`._song.wav` beside every file it copies to a foreign disk, which has the
right extension and is not audio, and fails only once someone presses OK -
and symbolic links to folders, so a link back up the tree cannot make the
walk run forever. Labels use `/` on Windows too.

`video_files()` still offers one entry per clip, but a clip is now a name
*within a folder*: `Prelinger/clip.mp4` and `Prelinger/clip.ts` are one
entry, and a `clip.mp4` in each of two folders is two. The ATSC transmitter
used to find its saved clip by bare name, which with subfolders could land
on a same-named clip in another folder; it tries the exact file first now
and falls back to the name, so a choice saved where it was a `.ts` still
finds the `.mp4` elsewhere.

**The list is built when a dialog opens**, not while one is open: the
launcher makes a fresh dialog on every click, so a new file or a new media
folder shows up the next time an app is opened. A running FM + RDS
transmitter's Next Track steps through the list its dialog was opened
with.

```sh
python scripts/test_media.py    # no radio, no display; Linux and Windows
```

builds a throwaway folder with every case above and checks what comes
back, including a link that points back up the tree.

### MP3 as well as WAV

Every audio list offers `.mp3` beside `.wav` (`AUDIO` in `apps/media.py`),
and all seven apps that play a file play either. A WAV plays exactly as it
always did and needs nothing installed; an MP3 is decoded by ffmpeg into a
pipe (`apps/audio_file.py`), as the video transmitters already take a
clip's soundtrack.

- **Decoded to 48 kHz, not played at its own rate.** Most MP3s are 44.1
  kHz, and AM Audio, FM Audio, PPM-OOK, FM Subcarrier and FM + RDS assume
  48 kHz of everything; played at their own rate they would come out 8.8%
  sharp, deviation and all. `scripts/test_audio_files.py` makes its MP3s at
  44.1 kHz for exactly that reason, so a missed resample reads 440 Hz as
  479 and fails.
- **One entry per song.** `choices()` lists a name once per folder, keeping
  the extension earliest in the list it is given - so a song kept as both
  `.wav` and `.mp3` is offered once, as the WAV. Otherwise the list shows
  two identical labels, the trap the `.mp4`/`.ts` video twins fell into.
- **The FM + RDS transmitter decodes on a thread of its own.** Its audio is
  a Python block that swaps files in place for Next Track, and reading
  ffmpeg's pipe from `work()` would stall the whole flowgraph - pilot and
  RDS with it - whenever the pipe ran dry, which it does at the start of
  every track while ffmpeg opens the file. On Windows a pipe holds 4 KB,
  10 ms of stereo float. `PcmReader` keeps a second decoded ahead and never
  waits; measured, it ran short by 0 frames across a Next Track swap, on
  the i9 and on the Windows laptop alike.
- **An MP3's ffmpeg is killed, not asked to stop, when the app closes.** A
  decoder blocked writing to a full pipe - its normal state, running ahead
  of playback - ignores SIGTERM, and waiting out the timeout held up
  closing the window by two seconds. Each app closes its source in
  `closeEvent`; left alone, ffmpeg would sit on the pipe until the
  launcher exits.
- A mono source takes an MP3 down to mono as (L+R)/2, where a stereo WAV
  still gives only its left channel, as `wavfile_source` always has.
- **FM + RDS Now Playing comes from the song's own tags.** `song()` in
  `fmRdsTransmitter.py` reads title and artist with ffprobe (`track_tags`
  in `apps/audio_file.py` - ID3 in an MP3, the INFO chunk in a WAV) and
  sends them as RT+ artist and title, so a receiver shows "Bon Jovi" and
  "Livin' On A Prayer" rather than the file name "01 Livin' On A Prayer".
  A file with no title tag still sends its name, as before. ffprobe's
  output is decoded as UTF-8 explicitly, since the locale's encoding on
  Windows is cp1252.
- **Everything sent as RDS text is made plain ASCII first** (`rds_text` in
  `apps/rds_encode.py`, in all four of the encoder's text setters, so typed
  PS and RadioText too). The encoder packs a character into a byte with
  `ord()`. Measured through the encoder and decoder, sent as they were, the
  curly apostrophe in a tag arrived as a space and "Motley Crue" with its
  umlauts as "M tley Cr e", every block clean and the letters silently
  gone; and Latin-1 accents would be read by a car radio from RDS's own
  table above 127 (IEC 62106 annex E), as other letters. So quotes and
  dashes are straightened, accents come off, "ss" and the like are spelt
  out, and what is left is dropped. RT+ tags are counted after that, since
  they are offsets into the text actually sent.
- **An artist beside the title exposed two faults in the encoder**, both
  unseen while the artist was always empty. An RT+ group's second tag has
  a 5-bit length - 32 characters - and the artist went first, so a title
  over 32 characters went out cut to its low five bits: a 39-character
  title arrived as "Symphon", and the receiver rightly refused half a
  word. The title is the first tag now and the artist, fitted to 30, the
  second. And "artist - title" longer than RadioText's 64 was cut to 64
  with its tags still pointing past the end; an 84-character line arrived
  as no artist and no title at all, while the transmitter's On Air box
  claimed the whole title. `set_now_playing` fits the line first, cutting
  the title at a word. `test_audio_files.py` sends both through the
  encoder and decoder.

```sh
python scripts/test_audio_files.py            # tones it encodes itself
python scripts/test_audio_files.py song.mp3   # ... and a real one too
```

**Verified** on the Windows laptop against a folder of real MP3s it had
been ignoring: 14 songs listed where the WAV-only pickers found none, a
real track decoded in stereo, and every check above passing on that slow
machine.
