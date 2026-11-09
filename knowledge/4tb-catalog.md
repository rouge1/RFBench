# What this project built that is on the 4TB drive

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

A catalog of the files this repository's sessions made that sit on the
external 4TB drive (exFAT, mounted at `/media/user/4TB`), taken on
2026-11-05. Most of it is Bluetooth Classic test signals for
bluey-ox-walker. Why each set exists, and what bluey found with it, is in
[bluey-test-signals](bluey-test-signals.md); this file says where the
files are, how big they are, and what made them.

How a set was attributed: every synthetic sidecar (`.json` beside a
`.cf32`) names its `generator`, `SDR scripts/<script>.py`, and the
`generator_commit` it was made from. A folder counts as ours when its
sidecars say so, or when it holds recordings of a file we played. All IQ
is complex float32 (`cf32`), interleaved I/Q. Unless a row says otherwise,
the rate is 40 MS/s and the centre is 2439.0 or 2441.0 MHz.

**Ask before deleting anything on this drive**, even a partial set of our
own. Several folders are the only copy, and bluey's grading refers to
them by name.

## The drive at a glance

| Folder | Files | IQ | What |
|---|---|---|---|
| `sdr-synth-tmp/` | 2,018, and 5,964 more in `rg-archive/` | 545 synthetic `.cf32`, 1.04 TB; 159 bench `.cf32` (recordings, crops and the files played), 1.04 TB | ours: every synthetic set since 2026-10-05, the bench recordings, scripts and an archive |
| `sdr-archive/stage3-ota-raw-20261004/` | 24 | 6 `.cf32`, 39.7 GB | ours: stage 3 raw over-the-air captures |
| `sdr-archive/rf-games-20261030-night2/` | 345 | 63 `.cf32`, 84.3 GB | ours: RF games night 2 with fm-rec2, cable only |
| `data/iq/ota_*` | 7 | 7 `.cf32` | ours: copies of the stage 2 and 3 captures, in bluey's tree |
| `data/synth-batch2/mixed_v2/` | 4 | 2 `.cf32`, 4.0 GB | ours: a copy of `sdr-synth-tmp/mixed/` |
| `data/` (`iq/`, `btsnoop/`, `sidecar/`) | 19 | 3 `.cf32`, 70.1 GB | ours: three 2026-11-08 daPie and ESP32 role-switch recordings (2441, 2439 and 2439 MHz), see [below](#a-real-link-recorded-2026-11-08-a-mid-link-role-switch) |
| everything else | | | not ours, see [the end](#on-the-drive-but-not-ours) |

The synthetic files from before 2026-10-05 (stages 1 and 3, `synth_*`
and `synth_hop20_*`) are not on this drive. They live in bluey's own
`data/` folder (74 `synth_*.cf32` in `/data/python/bluey-ox-walker/data/iq/`), and every one of
them can be made again from its generator commit.

## Synthetic Classic sets, 2026-10-05 to 10-07

Known-truth Classic Bluetooth captures built from the repo's `scripts/bt_synth_*.py` generators, one folder per question asked by bluey-ox-walker. Every `.cf32` has a `.json` sidecar with the truth; sidecar sizes match the IQ files (8 bytes per sample) in all 117 cases.

### `sdr-synth-tmp/dc/`

DH1 and DH5 bursts on channel 39 at the capture centre, for whether bluey's device-aware DC block should delete the centre channel. Built by `scripts/bt_synth_dc.py` at commit `c507a3a`, files of 2026-10-05. 800 bursts per file (200 for the `snr16` files), SNR stepping 8, 12, 16, 20 dB, 20 MS/s. The `dh1` files use seed 4008 and the `dh5` files seed 4001. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_dc_dh1_ch38_clean.cf32` | 1.6 | 20 | 2441.0 | DH1, channel 38 (2440 MHz), no tone. Control |
| `synth_dc_dh1_ch39_clean.cf32` | 1.6 | 20 | 2441.0 | DH1, channel 39 (0 Hz), no tone. Reference for the spur files |
| `synth_dc_dh1_ch39_cw15.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 15 dB over noise per bin (-2.1 dB in 1 MHz) |
| `synth_dc_dh1_ch39_cw25.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 25 dB per bin (7.9 dB in 1 MHz) |
| `synth_dc_dh1_ch39_cw35.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 35 dB per bin (17.9 dB in 1 MHz) |
| `synth_dc_dh1_ch39_drift15.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz over the file, 15 dB per bin |
| `synth_dc_dh1_ch39_drift25.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz, 25 dB per bin |
| `synth_dc_dh1_ch39_drift35.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz, 35 dB per bin |
| `synth_dc_dh1_ch39_snr16_cfo0.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset 0 Hz |
| `synth_dc_dh1_ch39_snr16_cfomi30k.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset -30 kHz |
| `synth_dc_dh1_ch39_snr16_cfopl30k.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset +30 kHz |
| `synth_dc_dh1_ch40_clean.cf32` | 1.6 | 20 | 2441.0 | DH1, channel 40 (2442 MHz), no tone. Control |
| `synth_dc_dh1_ch43_clean.cf32` | 1.6 | 20 | 2441.0 | DH1, channel 43 (2445 MHz), no tone. Control |
| `synth_dc_dh5_ch38_clean.cf32` | 1.6 | 20 | 2441.0 | DH5, channel 38 (2440 MHz), no tone. Control |
| `synth_dc_dh5_ch39_clean.cf32` | 1.6 | 20 | 2441.0 | DH5, channel 39 (0 Hz), no tone. Reference |
| `synth_dc_dh5_ch39_cw15.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 15 dB per bin |
| `synth_dc_dh5_ch39_cw25.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 25 dB per bin |
| `synth_dc_dh5_ch39_cw35.cf32` | 1.6 | 20 | 2441.0 | CW tone at 0 Hz, 35 dB per bin |
| `synth_dc_dh5_ch39_drift15.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz, 15 dB per bin |
| `synth_dc_dh5_ch39_drift25.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz, 25 dB per bin |
| `synth_dc_dh5_ch39_drift35.cf32` | 1.6 | 20 | 2441.0 | Tone rising 0 to +1 kHz, 35 dB per bin |
| `synth_dc_dh5_ch39_snr16_cfo0.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset 0 Hz |
| `synth_dc_dh5_ch39_snr16_cfomi30k.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset -30 kHz |
| `synth_dc_dh5_ch39_snr16_cfopl30k.cf32` | 0.4 | 20 | 2441.0 | 200 bursts at 16 dB, carrier offset +30 kHz |
| `synth_dc_dh5_ch40_clean.cf32` | 1.6 | 20 | 2441.0 | DH5, channel 40 (2442 MHz), no tone. Control |
| `synth_dc_dh5_ch43_clean.cf32` | 1.6 | 20 | 2441.0 | DH5, channel 43 (2445 MHz), no tone. Control |

Non-IQ files: one `.json` sidecar per `.cf32`, and nothing else.

### `sdr-synth-tmp/dc40/`

The DC set at 40 MS/s: DH5 on channel 39 at 12 dB and 20 dB, with and without a 25 dB CW tone at 0 Hz. Built by `scripts/bt_synth_dc.py` at `c507a3a`, files of 2026-10-05. 800 bursts per file, seed 4027, 12.5 ms apart. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_dc40_dh5_ch39_snr12_clean.cf32` | 3.2 | 40 | 2441.0 | DH5 at 12 dB, no tone. Control |
| `synth_dc40_dh5_ch39_snr12_cw25.cf32` | 3.2 | 40 | 2441.0 | Same bursts, CW tone at 0 Hz, 25 dB per bin (10.9 dB in 1 MHz) |
| `synth_dc40_dh5_ch39_snr20_clean.cf32` | 3.2 | 40 | 2441.0 | DH5 at 20 dB, no tone. Control |
| `synth_dc40_dh5_ch39_snr20_cw25.cf32` | 3.2 | 40 | 2441.0 | Same bursts, CW tone at 0 Hz, 25 dB per bin (10.9 dB in 1 MHz) |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/sync/`

Hopping DH1 bursts with planted sync-word bit errors, for how often a receiver recognises a LAP whose sync word has errors, and one 120 s file of noise with no bursts. Built by `scripts/bt_synth_sync.py` at `c507a3a`, files of 2026-10-05. LAP `0x9E8B33`, UAP 0x47, map 31-50, 20 dB, seed 5001 for the three error files. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_hop20_dh1_syncerr_clean.cf32` | 0.3 | 40 | 2441.0 | 800 bursts, no planted errors. Control |
| `synth_hop20_dh1_syncerr_first1.cf32` | 0.3 | 40 | 2441.0 | Only burst 0 has a sync-word error (1 bit) |
| `synth_hop20_dh1_syncerr_mixed.cf32` | 0.3 | 40 | 2441.0 | Burst 0 has 1 error; the other bursts have 0, 1 or 2 |
| `synth_noise_only_20msps_120s.cf32` | 19.2 | 20 | 2441.0 | No bursts. 120 s of white noise at the same level per MHz, seed 5004 |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/pairs/`

400 master and slave DH1 pairs, the slave one slot after its master on the same channel, for recovering the clock and the roles from the channels. Built by `scripts/bt_synth_pairs.py` at `c507a3a`, file of 2026-10-05. 800 bursts, 40 MS/s, master SNR 20 dB, 16 pairs on channel 39, 5 back-to-back couples on one channel. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_pairs_dh1_hop20.cf32` | 0.3 | 40 | 2441.0 | 400 pairs, idle gaps of 0, 1 or 2 pair-periods |

Non-IQ files: `synth_pairs_dh1_hop20.json`, the sidecar.

### `sdr-synth-tmp/page/`

Page and inquiry exchanges with their FHS packets on the adapted map 31-50, for the FHS and page-scan receiver. Built by `scripts/bt_synth_page.py` at `c7c283e`, files of 2026-10-06. 40 MS/s, 20 dB. Paged device LAP `0x6B1D3C`, UAP 0x47; master LAP `0x112233`, UAP 0x55. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_inq_exch_hop20.cf32` | 1.8 | 40 | 2441.0 | 200 inquiry exchanges, 2,358 bursts: GIAC IDs and the scanner's FHS response, no follow-up |
| `synth_page_exch_hop20.cf32` | 12.4 | 40 | 2441.0 | 200 page exchanges, 46,816 bursts: two IDs per TX slot, ID response, FHS, ID acknowledgement, 110 follow-up packets each. Clock lock unique in 173 of 200 |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/interf/`

A DH5 train (800 bursts, seed 6101) with no interferer, and seven copies with an interferer added, so each interferer's effect is the difference from the clean file. Built by `scripts/bt_synth_interf.py` at `c7c283e`, files of 2026-10-06. 40 MS/s, 20 dB, map 31-50. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_hop20_dh5_int_clean.cf32` | 1.0 | 40 | 2441.0 | No interferer. Control |
| `synth_hop20_dh5_int_cw43_p10.cf32` | 1.0 | 40 | 2441.0 | Continuous tone on channel 43 (+4 MHz), 10 dB over noise in 1 MHz |
| `synth_hop20_dh5_int_cw43_p20.cf32` | 1.0 | 40 | 2441.0 | Same tone on channel 43, 20 dB |
| `synth_hop20_dh5_int_cw49_p10.cf32` | 1.0 | 40 | 2441.0 | Continuous tone on channel 49 (+10 MHz), 10 dB |
| `synth_hop20_dh5_int_cw49_p20.cf32` | 1.0 | 40 | 2441.0 | Same tone on channel 49, 20 dB |
| `synth_hop20_dh5_int_wifi_const_p20.cf32` | 1.0 | 40 | 2441.0 | Wi-Fi-like noise, 2452-2461 MHz, 20 dB per MHz, constant |
| `synth_hop20_dh5_int_wifi_bursty_p20.cf32` | 1.0 | 40 | 2441.0 | Same noise in frames of 0.3-3 ms at 30 % duty (542 frames), 20 dB per MHz |
| `synth_hop20_dh5_int_both_p20.cf32` | 1.0 | 40 | 2441.0 | Two tones and the bursty Wi-Fi-like noise, 20 dB (3 interferers) |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/acl/`

Master packets only, DM1, DM3 and DM5, for DM and LMP decoding at five SNR levels, with a DH5 control. Built by `scripts/bt_synth_acl.py` at `dc220fc`, files of 2026-10-06. 40 MS/s, map 31-50, LAP `0x112233`, UAP 0x55, seed 8101. The level files are paired: same schedule, clocks, channels, payloads and noise, scaled in signal only. Each level file has 204 bursts: 68 DM1 (LMP PDUs), 68 DM3, 68 DM5. These files include the air bits. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_acl_dh5_hop20_clean_snr20.cf32` | 0.3 | 40 | 2441.0 | 204 DH5 bursts at 20 dB, no LMP. Control |
| `synth_acl_dm_hop20_snr16p5.cf32` | 0.3 | 40 | 2441.0 | DM1, DM3, DM5 at 16.5 dB |
| `synth_acl_dm_hop20_snr15p0.cf32` | 0.3 | 40 | 2441.0 | DM1, DM3, DM5 at 15.0 dB |
| `synth_acl_dm_hop20_snr14p0.cf32` | 0.3 | 40 | 2441.0 | DM1, DM3, DM5 at 14.0 dB |
| `synth_acl_dm_hop20_snr13p5.cf32` | 0.3 | 40 | 2441.0 | DM1, DM3, DM5 at 13.5 dB |
| `synth_acl_dm_hop20_snr10p5.cf32` | 0.3 | 40 | 2441.0 | DM1, DM3, DM5 at 10.5 dB |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/between/`

The clean interferer train of `interf` plus one tone between two channel centres, to test a tone that falls on a channel edge. Built by `scripts/bt_synth_between.py` at `dc220fc`, files of 2026-10-06. 800 bursts, DH5, seed 6101, 40 MS/s. The sidecar gives each tone's offset from channel centre. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_hop20_dh5_int_cwb4p25_p20.cf32` | 1.0 | 40 | 2441.0 | Tone at +4.25 MHz, 20 dB |
| `synth_hop20_dh5_int_cwb4p5_p10.cf32` | 1.0 | 40 | 2441.0 | Tone at +4.5 MHz, 10 dB (on the edge of channels 43 and 44) |
| `synth_hop20_dh5_int_cwb4p5_p20.cf32` | 1.0 | 40 | 2441.0 | Tone at +4.5 MHz, 20 dB |
| `synth_hop20_dh5_int_cwb4p75_p20.cf32` | 1.0 | 40 | 2441.0 | Tone at +4.75 MHz, 20 dB |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/conf/`

Page and inquiry exchanges on the spec's own hop sequences, cut to the capture window (channels 27-51, 2429-2453 MHz), for the page and inquiry receiver. Built by `scripts/bt_synth_conf.py` at `f0b4b43`, files of 2026-10-06. 40 MS/s, 200 exchanges per file. The sidecar's `conformance` key lists what is not modelled. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_conf_inq_hop_win.cf32` | 2.2 | 40 | 2441.0 | Inquiry exchanges: 2,042 bursts, 819 of them rendered in the window |
| `synth_conf_page_hop_win.cf32` | 12.9 | 40 | 2441.0 | Page exchanges: 46,414 bursts, 14,808 rendered in the window. FHS in window in 88 exchanges. Clock lock unique in 159 of 200 |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/fade/`

204 bursts per file (51 DM1 with LMP, 51 DM3, 51 DM5, 51 page-response FHS), the same bursts, channels, payloads and noise in all 13, under one distortion each. Built by `scripts/bt_synth_fade.py` at `67ec384`, files of 2026-10-06. 40 MS/s, seed 9101. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_fade_ref_snr18.cf32` | 0.2 | 40 | 2441.0 | No distortion, AWGN at 18 dB. Reference |
| `synth_fade_ref_snr24.cf32` | 0.2 | 40 | 2441.0 | No distortion, AWGN at 24 dB. Reference |
| `synth_fade_ray_fd100_snr18.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 100 Hz, 18 dB |
| `synth_fade_ray_fd100_snr24.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 100 Hz, 24 dB |
| `synth_fade_ray_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 300 Hz, 18 dB |
| `synth_fade_ray_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 300 Hz, 24 dB |
| `synth_fade_ray_fd800_snr18.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 800 Hz, 18 dB |
| `synth_fade_ray_fd800_snr24.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 800 Hz, 24 dB |
| `synth_fade_rice6_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Flat Rician fading, K 6 dB, Doppler 300 Hz, 18 dB |
| `synth_fade_rice6_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Flat Rician fading, K 6 dB, Doppler 300 Hz, 24 dB |
| `synth_fade_nb_sir0_snr24.cf32` | 0.2 | 40 | 2441.0 | Narrowband FM-like interferer on about half the bursts, SIR 0 dB, 24 dB |
| `synth_fade_nb_sir6_snr24.cf32` | 0.2 | 40 | 2441.0 | Same, SIR 6 dB |
| `synth_fade_nb_sir12_snr24.cf32` | 0.2 | 40 | 2441.0 | Same, SIR 12 dB |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/fade2/`

The 13 distortions of `fade` again at seed 9102: new bursts, channels, payloads, FHS fields, noise, fades and interferer hits, paired inside the set to the `_s2` references. Built by `scripts/bt_synth_fade.py` at `f134b7c`, a later commit than the `fade` set's `67ec384`, files of 2026-10-06. 40 MS/s. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_fade_ref_snr18_s2.cf32` | 0.2 | 40 | 2441.0 | No distortion, AWGN at 18 dB. Reference |
| `synth_fade_ref_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | No distortion, AWGN at 24 dB. Reference |
| `synth_fade_ray_fd100_snr18_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 100 Hz, 18 dB |
| `synth_fade_ray_fd100_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 100 Hz, 24 dB |
| `synth_fade_ray_fd300_snr18_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 300 Hz, 18 dB |
| `synth_fade_ray_fd300_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 300 Hz, 24 dB |
| `synth_fade_ray_fd800_snr18_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 800 Hz, 18 dB |
| `synth_fade_ray_fd800_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rayleigh fading, Doppler 800 Hz, 24 dB |
| `synth_fade_rice6_fd300_snr18_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rician fading, K 6 dB, Doppler 300 Hz, 18 dB |
| `synth_fade_rice6_fd300_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Flat Rician fading, K 6 dB, Doppler 300 Hz, 24 dB |
| `synth_fade_nb_sir0_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Narrowband FM-like interferer, SIR 0 dB, 24 dB |
| `synth_fade_nb_sir6_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Same, SIR 6 dB |
| `synth_fade_nb_sir12_snr24_s2.cf32` | 0.2 | 40 | 2441.0 | Same, SIR 12 dB |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/mp/`

Multipath versions of the `fade` reference bursts (seed 9101): each burst is a sum of independent Rayleigh paths with a tapped delay line, at Doppler 100 or 300 Hz and 18 or 24 dB. Built by `scripts/bt_synth_multipath.py` at `bf1cdb9`, files of 2026-10-06. 40 MS/s, 204 bursts per file. Each file is paired with `fade_ref_snr18` or `fade_ref_snr24`. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_mp_p2a_fd100_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p2a (2 paths, RMS delay spread 0.09 us), Doppler 100 Hz, 18 dB |
| `synth_mp_p2a_fd100_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p2a, Doppler 100 Hz, 24 dB |
| `synth_mp_p2a_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p2a, Doppler 300 Hz, 18 dB |
| `synth_mp_p2a_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p2a, Doppler 300 Hz, 24 dB |
| `synth_mp_p2b_fd100_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p2b (2 paths, RMS delay spread 0.47 us), Doppler 100 Hz, 18 dB |
| `synth_mp_p2b_fd100_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p2b, Doppler 100 Hz, 24 dB |
| `synth_mp_p2b_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p2b, Doppler 300 Hz, 18 dB |
| `synth_mp_p2b_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p2b, Doppler 300 Hz, 24 dB |
| `synth_mp_p3_fd100_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p3 (3 paths, RMS delay spread 0.35 us), Doppler 100 Hz, 18 dB |
| `synth_mp_p3_fd100_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p3, Doppler 100 Hz, 24 dB |
| `synth_mp_p3_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p3, Doppler 300 Hz, 18 dB |
| `synth_mp_p3_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p3, Doppler 300 Hz, 24 dB |
| `synth_mp_p4_fd100_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p4 (4 paths, RMS delay spread 0.32 us), Doppler 100 Hz, 18 dB |
| `synth_mp_p4_fd100_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p4, Doppler 100 Hz, 24 dB |
| `synth_mp_p4_fd300_snr18.cf32` | 0.2 | 40 | 2441.0 | Profile p4, Doppler 300 Hz, 18 dB |
| `synth_mp_p4_fd300_snr24.cf32` | 0.2 | 40 | 2441.0 | Profile p4, Doppler 300 Hz, 24 dB |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/mixed/`

Two blind-test captures: eight unsynchronised piconets (six of them ours) and three page joiners, on the map 31-50, with collisions. Built by `scripts/bt_synth_mixed.py` at `a74a7e8`, files of 2026-10-07. 40 MS/s, 6.29 s each, 11 devices. Each sidecar is the answer key for its blind test: it gives the devices, `ours`, and per-burst identity, and its contents are not copied here. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_mixed_blind_a.cf32` | 2.0 | 40 | 2441.0 | Seed 9201, 17,487 bursts, 1,452 collided (8.3 %) |
| `synth_mixed_blind_b.cf32` | 2.0 | 40 | 2441.0 | Seed 9202, 18,389 bursts, 1,656 collided (9.0 %) |

Non-IQ files: `synth_mixed_blind_a.json` and `synth_mixed_blind_b.json`, the answer-key sidecars.

### `sdr-synth-tmp/clk/`

The 800 DH5 bursts of `interf`'s clean file as a receiver with its sample clock off by a few ppm (`clk`), or with its clock and LO both off (`clklo`), would see them. Built by `scripts/bt_synth_clk.py` at `b344854`, files of 2026-10-07. 40 MS/s, seed 6101, map 31-50. In `clklo` every carrier also moves by the stated amount. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_hop20_dh5_clk_m200.cf32` | 1.0 | 40 | 2441.0 | Sample clock -200 ppm, no carrier offset |
| `synth_hop20_dh5_clk_m100.cf32` | 1.0 | 40 | 2441.0 | Sample clock -100 ppm |
| `synth_hop20_dh5_clk_m50.cf32` | 1.0 | 40 | 2441.0 | Sample clock -50 ppm |
| `synth_hop20_dh5_clk_m20.cf32` | 1.0 | 40 | 2441.0 | Sample clock -20 ppm |
| `synth_hop20_dh5_clk_p20.cf32` | 1.0 | 40 | 2441.0 | Sample clock +20 ppm |
| `synth_hop20_dh5_clk_p50.cf32` | 1.0 | 40 | 2441.0 | Sample clock +50 ppm |
| `synth_hop20_dh5_clk_p100.cf32` | 1.0 | 40 | 2441.0 | Sample clock +100 ppm |
| `synth_hop20_dh5_clk_p200.cf32` | 1.0 | 40 | 2441.0 | Sample clock +200 ppm |
| `synth_hop20_dh5_clklo_m100.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO -100 ppm; carrier +244.1 kHz |
| `synth_hop20_dh5_clklo_m50.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO -50 ppm; carrier +122.0 kHz |
| `synth_hop20_dh5_clklo_m20.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO -20 ppm; carrier +48.8 kHz |
| `synth_hop20_dh5_clklo_p20.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO +20 ppm; carrier -48.8 kHz |
| `synth_hop20_dh5_clklo_p50.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO +50 ppm; carrier -122.0 kHz |
| `synth_hop20_dh5_clklo_p100.cf32` | 1.0 | 40 | 2441.0 | Sample clock and LO +100 ppm; carrier -244.1 kHz |

Non-IQ files: one `.json` sidecar per `.cf32`.

### `sdr-synth-tmp/pagemix/`

A 31.5 s capture of ten unsynchronised piconets in NULL and POLL heavy traffic, with 20 joiner exchanges (12 page, 8 inquiry) placed in dense traffic, for finding page and inquiry events. Built by `scripts/bt_synth_pagemix.py` at `7fb5576`, file of 2026-10-07. 40 MS/s, seed 9301, 30 devices, 14.2 % of bursts collided. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_pagemix.cf32` | 10.1 | 40 | 2441.0 | 241,714 bursts: ten piconets and 20 joiners (12 page, 8 inquiry) |

Non-IQ files: `synth_pagemix.bursts.jsonl.gz` (25 MB, one burst per line, not opened for this catalog) and `synth_pagemix.json`, the sidecar.

### `sdr-synth-tmp/connonly/`

The negative set for `pagemix`: the same dense traffic with no joiner, no ID and no FHS, so every page or inquiry event reported on it is false. Built by `scripts/bt_synth_pagemix.py` at `7fb5576`, file of 2026-10-07. 40 MS/s, seed 9302, 10 devices, 12.65 % of bursts collided. For bluey-ox-walker. See [bluey-test-signals](bluey-test-signals.md).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_connonly.cf32` | 20.1 | 40 | 2441.0 | 626,218 bursts, ten piconets, no joiners |

Non-IQ files: `synth_connonly.bursts.jsonl.gz` (53 MB, not opened for this catalog) and `synth_connonly.json`, the sidecar.

## Synthetic slot, burst-end and LAP sets, 2026-10-12 to 10-18

Nine folders of synthetic Bluetooth Classic captures for bluey-ox-walker, built from the `bt_synth_*` generators between 2026-10-12 and 2026-10-18. Every `.cf32` has a `.json` sidecar with its truth; "What differs" is read from the sidecar keys and the manifests.

### `sdr-synth-tmp/slotb/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_slotb.py` at commit 2f82cb0 on 2026-10-12 (files 11:44 to 11:48). For bluey-ox-walker: the slot-boundary cases for the blind UAP recovery defect, and the tail and drift cases for the same test. LAP 0x635FCA and UAP 0x47 in every file except the D set. The `D_` files use LAPs of their own.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb_A_20msps.cf32` | 1.6 | 20 | 2441.0 | single-slot exchanges; grid 3.25 us; drift +23 ppm; 20 MS/s; drift +23 ppm, unlike the other A files (0) |
| `synth_slotb_A_312p75.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 312.75 us; drift 0 ppm |
| `synth_slotb_A_50p25.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 50.25 us; drift 0 ppm |
| `synth_slotb_B_down.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 3 us; drift +23 ppm |
| `synth_slotb_B_down_cfo.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 3 us; drift +20 ppm; CFO 48.78 kHz |
| `synth_slotb_B_late.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 100 us; drift +23 ppm; offset 100 us, crossing at 4.37 s |
| `synth_slotb_B_up.cf32` | 3.2 | 40 | 2439.0 | single-slot exchanges; grid 622 us; drift -23 ppm |
| `synth_slotb_C1_50p0.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 50 us; drift 0 ppm; no tail |
| `synth_slotb_C1_50p25.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 50.25 us; drift 0 ppm; no tail |
| `synth_slotb_C1_cross.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 3 us; drift +23 ppm; no tail; drift crosses 0 at 0.150 s |
| `synth_slotb_C2_0_cross.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 3 us; drift +23 ppm; held tail at 0 dB; drift crosses 0 at 0.150 s |
| `synth_slotb_C2m10_50p0.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 50 us; drift 0 ppm; held tail at -10 dB |
| `synth_slotb_C2m10_50p25.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 50.25 us; drift 0 ppm; held tail at -10 dB |
| `synth_slotb_C2m10_cross.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 3 us; drift +23 ppm; held tail at -10 dB; drift crosses 0 at 0.150 s |
| `synth_slotb_C2m10_f50k_cross.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 3 us; drift +23 ppm; held tail at -10 dB; tail moved to +50 kHz; drift crosses 0 at 0.150 s |
| `synth_slotb_C2m20_cross.cf32` | 1.9 | 40 | 2439.0 | page IDs only; grid 3 us; drift +23 ppm; held tail at -20 dB; drift crosses 0 at 0.150 s |
| `synth_slotb_D_diff_s1.cf32` | 8.3 | 40 | 2439.0 | page IDs of LAP 0x8C1ACA (UAP 0xEC), then single-slot exchanges; grid 3 us; drift +23 ppm; connection LAP 0x0DE4A7 never sends an ID |
| `synth_slotb_D_same_s1.cf32` | 8.3 | 40 | 2439.0 | page IDs of LAP 0x8C1ACA (UAP 0xEC), then single-slot exchanges; grid 3 us; drift +23 ppm |
| `synth_slotb_D_same_s2.cf32` | 8.3 | 40 | 2439.0 | page IDs of LAP 0x15CBCE (UAP 0x24), then single-slot exchanges; grid 3 us; drift +23 ppm |
| `synth_slotb_D_same_s3.cf32` | 8.3 | 40 | 2439.0 | page IDs of LAP 0x3A0FDA (UAP 0x67), then single-slot exchanges; grid 3 us; drift +23 ppm |

Non-IQ files: none. There is no manifest, log or SHA256SUMS.txt in this folder; the `.json` sidecars are the only record.

### `sdr-synth-tmp/slotb2/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_slotb2.py` at commit b828969 (files 21:37 to 21:42 on 2026-10-12). For bluey-ox-walker: page responses with truth UAPs, and the ID-share and connection-first cases. Every file has 20 dB SNR and no tail.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_C_20msps.cf32` | 1.6 | 20 | 2441.0 | LAP 0x031AB0, UAP 0x36; page IDs only; master -30 kHz; grid 50.25 us |
| `synth_slotb2_R_20msps_s1.cf32` | 1.6 | 20 | 2441.0 | LAP 0x42E09D, UAP 0xFF; repeated page exchanges (ID, ID response, FHS, ack, POLL, NULL answers); master -30 kHz |
| `synth_slotb2_R_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x67B138, UAP 0xCF; repeated page exchanges (ID, ID response, FHS, ack, POLL, NULL answers); master -30 kHz |
| `synth_slotb2_R_s2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x7DE52A, UAP 0x7D; repeated page exchanges (ID, ID response, FHS, ack, POLL, NULL answers); master +30 kHz |
| `synth_slotb2_R_s3.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2B357D, UAP 0xCB; repeated page exchanges (ID, ID response, FHS, ack, POLL, NULL answers); master 0 kHz |
| `synth_slotb2_X_bda_id10_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0xAB45C8, UAP 0xC2; connection and ID train all file, ID share 10 %; master -30 kHz |
| `synth_slotb2_X_bda_id50_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492, UAP 0x15; connection and ID train all file, ID share 50 %; master -30 kHz |
| `synth_slotb2_X_bda_id87_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB, UAP 0x5F; connection and ID train all file, ID share 87 %; master -30 kHz |
| `synth_slotb2_X_first_id10_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0xBC64C6, UAP 0x1E; 4 s connection, then ID train on the same LAP, ID share 10 %; master -30 kHz |
| `synth_slotb2_X_first_id10_s2.cf32` | 3.8 | 40 | 2439.0 | LAP 0xAB25D4, UAP 0xFF; 4 s connection, then ID train on the same LAP, ID share 10 %; master +30 kHz |
| `synth_slotb2_X_first_id50_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x447C60, UAP 0xFF; 4 s connection, then ID train on the same LAP, ID share 50 %; master -30 kHz |
| `synth_slotb2_X_first_id50_s2.cf32` | 3.8 | 40 | 2439.0 | LAP 0xAF353A, UAP 0xA1; 4 s connection, then ID train on the same LAP, ID share 50 %; master +30 kHz |
| `synth_slotb2_X_first_id87_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x028459, UAP 0xED; 4 s connection, then ID train on the same LAP, ID share 87 %; master -30 kHz |
| `synth_slotb2_X_first_id87_s2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x28E2C7, UAP 0x63; 4 s connection, then ID train on the same LAP, ID share 87 %; master +30 kHz |

Non-IQ files: none. No manifest, log or SHA256SUMS.txt in this folder.

### `sdr-synth-tmp/endlen/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_endlen.py` at commit 406c29f (files 15:06 to 15:33 on 2026-10-14), for the burst-end stage: a known on-air length for every packet, 20 dB SNR, no tail. The `_20msps` files are the 20 MS/s copies.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_endlen_E_ladder_20msps_s1.cf32` | 1.6 | 20 | 2441.0 | LAP 0x4098D4, UAP 0x06; every DH1, DM1 and AUX1 body length, one per burst; on air DH1 150-366 us, DM1 171-366 us, AUX1 134-366 us; master -30 kHz |
| `synth_endlen_E_ladder_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x740AD8, UAP 0xF8; every DH1, DM1 and AUX1 body length, one per burst; on air DH1 150-366 us, DM1 171-366 us, AUX1 134-366 us; master -30 kHz |
| `synth_endlen_E_long_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0xA80792, UAP 0xB5; DM3, DH3, DM5 and DH5 over their body ranges, with NULL and POLL; master -30 kHz |
| `synth_endlen_E_mix_20msps_s1.cf32` | 1.6 | 20 | 2441.0 | LAP 0x4E0241, UAP 0x42; mixed traffic: IDs, NULL, POLL, empty and full DH1/DM1, AUX1; master -30 kHz |
| `synth_endlen_E_mix_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x67D5D8, UAP 0x50; mixed traffic: IDs, NULL, POLL, empty and full DH1/DM1, AUX1; master -30 kHz |
| `synth_endlen_E_mix_s2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x87F52A, UAP 0xC7; mixed traffic: IDs, NULL, POLL, empty and full DH1/DM1, AUX1; master +30 kHz |

Non-IQ files: `run.log`, `SHA256SUMS.txt`.

### `sdr-synth-tmp/endlen_np/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_endlen.py --stem E_np_s1` at commit e00a82a (2026-10-16, 09:28 to 09:29), one more `endlen` file for the NULL/POLL share. The other `endlen` files were built at 406c29f.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_endlen_E_np_s1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x84B190, UAP 0x6E; NULL or POLL on 78.0 % of all bursts, half of the payload packets empty; no IDs; master -30 kHz |

Non-IQ files: `run.log`, `SHA256SUMS.txt`.

### `sdr-synth-tmp/multislot/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_multislot.py --set setn` at commit 6aeeb1a (2026-10-17, 23:18 to 23:34). For bluey-ox-walker part B2: multi-slot answers and the wrong-role rows. Six piconets: five on AFH over 31-50, one on the basic 79-channel sequence. The sidecars carry no LAP or master-offset keys; the manifest gives the per-file counts.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_setn_a.cf32` | 3.2 | 40 | 2441.0 | six piconets, five on AFH over 31-50; 28,205 bursts, 2,889 not rendered, 15.6 % of bursts collided; seed 9701 |
| `synth_setn_b.cf32` | 3.2 | 40 | 2441.0 | as setn_a with seed 9702; 28,276 bursts, 2,883 not rendered, 16.1 % collided |
| `synth_setn_c.cf32` | 1.6 | 20 | 2441.0 | 20 MS/s from a 40 MS/s render of channels 31-47 only (FIR and decimation); seed 9703; 23,887 bursts, 7,380 not rendered |
| `synth_setn_d.cf32` | 3.2 | 40 | 2441.0 | setn_a plus bursty interferer at +2 to +11 MHz (+20 dB per MHz while on, 30 % duty); equal to setn_a outside the interferer frames |

Non-IQ files: `manifest.json`, `run.log`, `SHA256SUMS.txt`, `verify.txt` (post-render checks, PASS on all four).

### `sdr-synth-tmp/samelap/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_slotb2.py --set SL` at commit 287635c, on 2026-10-16 (17:25 to 17:43), for bluey's same-LAP audit. Two LAPs (0x0466AB, 0x662492), master carrier offset 0 or -30 kHz, the symbol phase of the first IDs, and a noise seed. Cells with the same noise seed share one noise and carrier-phase realisation.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_S_0466AB_c0_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 0; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_c0_p0_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 0; noise seed 2; ID share 87 % |
| `synth_slotb2_S_0466AB_c0_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 10; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_c0_p10_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 10; noise seed 2; ID share 87 % |
| `synth_slotb2_S_0466AB_c0_p20_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 20; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_c0_p30_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master 0 kHz; symbol phase 30; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 0; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p0_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 0; noise seed 2; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p10_id10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 10; noise seed 1; ID share 10 % (low density, 9398 bursts) |
| `synth_slotb2_S_0466AB_cm30_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 10; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p10_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 10; noise seed 2; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p15_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 15; noise seed 1; ID share 87 %; generator 287635c-dirty |
| `synth_slotb2_S_0466AB_cm30_p20_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 20; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p25_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 25; noise seed 1; ID share 87 %; generator 287635c-dirty |
| `synth_slotb2_S_0466AB_cm30_p30_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 30; noise seed 1; ID share 87 % |
| `synth_slotb2_S_0466AB_cm30_p35_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 35; noise seed 1; ID share 87 %; generator 287635c-dirty |
| `synth_slotb2_S_0466AB_cm30_p5_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x0466AB; master -30 kHz; symbol phase 5; noise seed 1; ID share 87 %; generator 287635c-dirty |
| `synth_slotb2_S_662492_c0_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 0; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_c0_p0_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 0; noise seed 2; ID share 87 % |
| `synth_slotb2_S_662492_c0_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 10; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_c0_p10_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 10; noise seed 2; ID share 87 % |
| `synth_slotb2_S_662492_c0_p20_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 20; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_c0_p30_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master 0 kHz; symbol phase 30; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 0; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p0_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 0; noise seed 2; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 10; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p10_n2.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 10; noise seed 2; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p20_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 20; noise seed 1; ID share 87 % |
| `synth_slotb2_S_662492_cm30_p30_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x662492; master -30 kHz; symbol phase 30; noise seed 1; ID share 87 % |

Four sidecars say `generator_commit` `287635c-dirty`: `synth_slotb2_S_0466AB_cm30_p5_n1.json`, `..._cm30_p15_n1.json`, `..._cm30_p25_n1.json` and `..._cm30_p35_n1.json`. Their `.cf32` files carry the same name. The rest say `287635c`.

Non-IQ files: `manifest.json`, `run.log`, `SHA256SUMS.txt`.

### `sdr-synth-tmp/laptest/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_slotb2.py --set LT` at commit a894e02 on 2026-10-17 (21:43 to 21:49), for bluey's LAP test. LAPs 0x2044EF and 0x4073AD have a sync word with 24 ones; 0x2626FE, with 32 ones, is the control. Each LAP has a phase-0 and a phase-10 cell at 0 Hz and a phase-10 cell at -30 kHz.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_S_2044EF_c0_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2044EF (24 ones, negative imbalance); master 0 kHz; symbol phase 0; noise seed 1 |
| `synth_slotb2_S_2044EF_c0_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2044EF (24 ones, negative imbalance); master 0 kHz; symbol phase 10; noise seed 1 |
| `synth_slotb2_S_2044EF_cm30_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2044EF (24 ones, negative imbalance); master -30 kHz; symbol phase 10; noise seed 1 |
| `synth_slotb2_S_2626FE_c0_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2626FE (control, 32 ones); master 0 kHz; symbol phase 0; noise seed 1 |
| `synth_slotb2_S_2626FE_c0_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2626FE (control, 32 ones); master 0 kHz; symbol phase 10; noise seed 1 |
| `synth_slotb2_S_2626FE_cm30_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x2626FE (control, 32 ones); master -30 kHz; symbol phase 10; noise seed 1 |
| `synth_slotb2_S_4073AD_c0_p0_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x4073AD (24 ones, negative imbalance); master 0 kHz; symbol phase 0; noise seed 1 |
| `synth_slotb2_S_4073AD_c0_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x4073AD (24 ones, negative imbalance); master 0 kHz; symbol phase 10; noise seed 1 |
| `synth_slotb2_S_4073AD_cm30_p10_n1.cf32` | 3.8 | 40 | 2439.0 | LAP 0x4073AD (24 ones, negative imbalance); master -30 kHz; symbol phase 10; noise seed 1 |

Non-IQ files: `manifest.json`, `run.log`, `SHA256SUMS.txt`, and one `.cf32.verify.log` per file (all end `RESULT: PASS`). The notes say the set is 33 GB; the nine files come to 34.6 GB.

### `sdr-synth-tmp/rxoffset/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_rx_offset.py` (commit 40435b2) from the samelap and laptest files named in each row, on 2026-10-17 (21:53 to 22:20). For bluey: what a receiver with a wrong reference records. The sources are not in this folder.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_S_0466AB_c0_p0_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_0466AB_c0_p0_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_0466AB_c0_p0_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_0466AB_c0_p0_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |
| `synth_slotb2_S_0466AB_c0_p10_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_0466AB_c0_p10_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_0466AB_c0_p10_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_0466AB_c0_p10_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |
| `synth_slotb2_S_0466AB_c0_p10_n1__rx_ppmp23p8_dcp10.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_0466AB_c0_p10_n1.cf32`; receiver clock 23.8 ppm fast, plus a +10 dB LO spur at 0 Hz; paired check FAIL on carrier (worst 394 Hz against 300 Hz), positions pass |
| `synth_slotb2_S_2044EF_c0_p10_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_2044EF_c0_p10_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_2044EF_c0_p10_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_2044EF_c0_p10_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |
| `synth_slotb2_S_2626FE_c0_p10_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_2626FE_c0_p10_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_2626FE_c0_p10_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_2626FE_c0_p10_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |
| `synth_slotb2_S_4073AD_c0_p10_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_4073AD_c0_p10_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_4073AD_c0_p10_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `laptest/S_4073AD_c0_p10_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |
| `synth_slotb2_S_662492_c0_p0_n1__rx_offm58k.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_662492_c0_p0_n1.cf32`; receiver rotated -58 kHz, clock exact |
| `synth_slotb2_S_662492_c0_p0_n1__rx_ppmp23p8.cf32` | 3.8 | 40 | 2439.0 | from `samelap/S_662492_c0_p0_n1.cf32`; receiver clock 23.8 ppm fast (reads about -58 kHz) |

Non-IQ files: `manifest.json`, `run.log`, `SHA256SUMS.txt`, `verify_paired.txt`. The `dcp10` file fails the carrier part of the paired check (worst 394 Hz against a 300 Hz bound); the verify log puts this down to estimation noise from the +10 dB spur. Its positions pass.

### `sdr-synth-tmp/freshset/`

See [bluey-test-signals](bluey-test-signals.md). Built with `scripts/bt_synth_slotb2.py --set FR` at commit 2c64b19 on 2026-10-18 (20:16 to 21:38), for bluey's carrier-threshold replication: six LAPs chosen by a rule committed before the search, 32 files, one noise seed per file (3001 to 3032). Cell codes: `p0` grid 50.0 us (symbol phase 0); `p10` grid 50.25 us (phase 10); `p10b` as p10 with another noise realisation; `d23` +23 ppm clock drift, which walks the phase through all 40 samples; `h28` and `h35` modulation index 0.28 or 0.35 instead of 0.32 (from the manifest).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_F_20C0AA_{p0,p10,p10b,d23}_n3005-3008.cf32` (4 files) | 15.4 | 40 | 2439.0 | LAP 0x20C0AA, negative imbalance (24 ones); cells p0, p10, p10b, d23 |
| `synth_slotb2_F_4426BB_{p0,p10,p10b,d23,p10_h28,d23_h28,p10_h35,d23_h35}_n3017-3020,3025-3028.cf32` (8 files) | 30.8 | 40 | 2439.0 | LAP 0x4426BB, balanced, isolated 1s ahead (32 ones); cells p0, p10, p10b, d23, p10_h28, d23_h28, p10_h35, d23_h35 |
| `synth_slotb2_F_885614_{p0,p10,p10b,d23,p10_h28,d23_h28,p10_h35,d23_h35}_n3001-3004,3029-3032.cf32` (8 files) | 30.8 | 40 | 2439.0 | LAP 0x885614, negative imbalance (24 ones); cells p0, p10, p10b, d23, p10_h28, d23_h28, p10_h35, d23_h35 |
| `synth_slotb2_F_A9A2FB_{p0,p10,p10b,d23}_n3021-3024.cf32` (4 files) | 15.4 | 40 | 2439.0 | LAP 0xA9A2FB, balanced, isolated 0s ahead (32 ones); cells p0, p10, p10b, d23 |
| `synth_slotb2_F_DBB3FB_{p0,p10,p10b,d23}_n3013-3016.cf32` (4 files) | 15.4 | 40 | 2439.0 | LAP 0xDBB3FB, positive imbalance (40 ones); cells p0, p10, p10b, d23 |
| `synth_slotb2_F_EED5EF_{p0,p10,p10b,d23}_n3009-3012.cf32` (4 files) | 15.4 | 40 | 2439.0 | LAP 0xEED5EF, positive imbalance (40 ones); cells p0, p10, p10b, d23 |

Non-IQ files: `manifest.json`, `run.log`, `SHA256SUMS.txt`, and one `.cf32.verify.log` per file (all end `RESULT: PASS`).

## Synthetic sets, 2026-10-27 to 10-30: lost pairs, recall-confirm, controls, receiver sweep

Six folders on the drive, built between 2026-10-27 and 2026-10-30: two lost-pair sets and the recall-confirm blocks with their negative control (for bluey-ox-walker), then a set of controls and a receiver-offset sweep whose requester the notes do not record. 300 `.cf32` files, about 400 GB.

### `sdr-synth-tmp/lostpair/`

The first 2 s of the two FC stems, `F_13B6FA_d23_n3101` and `F_66C0A6_d23_n3102` (both on the +23 ppm drift cell), rendered for bluey's question whether detector F should become the default. Built by `scripts/bt_synth_slotb2.py` at generator_commit 9973432, files dated 2026-10-27 20:05 to 20:08. Each sidecar's `name` and `stem` are the stem's; its `lostpair` block names the file. See [bluey-test-signals](bluey-test-signals.md) (the lost-pair set).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `lp_13B6FA_{m58,p58,z00}_s{04..20}.cf32` | 17.5 | 40 | 2439.0 | 27 files. LAP 0x13B6FA, UAP 230. Master offset -58 / +58 / 0 kHz. SNR 4 to 20 dB in 2 dB steps. 1000 bursts rendered. |
| `lp_66C0A6_{m58,p58,z00}_s{04..20}.cf32` | 17.5 | 40 | 2439.0 | 27 files. LAP 0x66C0A6, UAP 234. Same offset and SNR grid. 1308 bursts rendered. |

Non-IQ files: 54 `.json` sidecars (about 4.7 MB each) and `SHA256SUMS.txt`.

### `sdr-synth-tmp/lostpair-d64727/`

The lost-pair test on LAP 0xD64727, which is where most of bluey's real lost pairs are and which no stem draws. Built by `scripts/bt_synth_slotb2.py --lap` at generator_commit 96f6502, files dated 2026-10-27 20:16 to 20:17. The first 4 s of the `F_66C0A6_d23_n3102` plan with the LAP forced (`lap_forced` in the sidecar). The `cell` block is still the stem's, so it says 66C0A6 and offset 0. See [bluey-test-signals](bluey-test-signals.md) (`--lap`).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `lp_D64727_{m58,p58,z00}_s{06,10,14,18}.cf32` | 15.4 | 40 | 2439.0 | 12 files. LAP 0xD64727, UAP 134. Master offset -58 / +58 / 0 kHz. SNR 6, 10, 14, 18 dB. 922 bursts rendered out of 6376 planned. |

Non-IQ files: 12 `.json` sidecars (about 9.5 MB each) and `SHA256SUMS.txt`.

### `sdr-synth-tmp/recall-confirm/`

The data for bluey's registered recall test of detector F against P, on three LAPs drawn for it: 0x0663EF, 0x5AF600 and 0xC4ED62. Every file is a 4 s plan of the `F_66C0A6_d23_n3102` stem, rendered with `--lap` (and `--cfo-ramp-hz` for block B). Each sidecar has a `recall_confirm` block with the block letter, the impairment and the seeds. That block, and the rewrite of each sidecar's `cell` to the file's own LAP, are not written by any script committed at 96f6502 or 7b08de8; they come from a step kept in `recall-confirm-private`. See [bluey-test-signals](bluey-test-signals.md) (the recall-confirm set).

#### `sdr-synth-tmp/recall-confirm/A/`

Block A: an ideal transmitter at 0 ppm, offsets and SNR only. Generator_commit 96f6502 as recorded; the files were written 2026-10-28 06:11 to 06:28, after 7b08de8.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `rcA_0663EF_{m30,m58,p30,p58,z00}_s{08..20}.cf32` | 45.0 | 40 | 2439.0 | 35 files. LAP 0x0663EF, UAP 47. Master offset -30 / -58 / +30 / +58 / 0 kHz. SNR 8 to 20 dB in 2 dB steps. 1923 bursts rendered. |
| `rcA_5AF600_{m30,m58,p30,p58,z00}_s{08..20}.cf32` | 45.0 | 40 | 2439.0 | 35 files. LAP 0x5AF600, UAP 12. Same grid. 2369 bursts rendered. |
| `rcA_C4ED62_{m30,m58,p30,p58,z00}_s{08..20}.cf32` | 45.0 | 40 | 2439.0 | 35 files. LAP 0xC4ED62, UAP 65. Same grid. 1031 bursts rendered. |

Non-IQ files: 105 `.json` sidecars (about 9 MB each) and `SHA256SUMS.txt`.

#### `sdr-synth-tmp/recall-confirm/B/`

Block B: the same three LAPs and offsets, at SNR 10, 14 and 18 dB, each with one impairment. `m20` and `p20` are a sample-clock offset of -20 and +20 ppm, with the carrier not following. `ramp` is a carrier ramp over the 4 s: +10 kHz on 0x0663EF and 0x5AF600, -10 kHz on 0xC4ED62. Generator_commit 7b08de8, files written 2026-10-28 06:28 to 06:43.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `rcB_0663EF_{m58,z00,p58}_s{10,14,18}_{m20,p20,ramp}.cf32` | 34.8 | 40 | 2439.0 | 27 files. LAP 0x0663EF, UAP 47. Offset -58 / 0 / +58 kHz. SNR 10 / 14 / 18 dB. Impairment -20 ppm, +20 ppm or carrier ramp +10 kHz. 1923 bursts rendered. |
| `rcB_5AF600_{m58,z00,p58}_s{10,14,18}_{m20,p20,ramp}.cf32` | 34.8 | 40 | 2439.0 | 27 files. LAP 0x5AF600, UAP 12. Same grid, ramp +10 kHz. 2369 bursts rendered. |
| `rcB_C4ED62_{m58,z00,p58}_s{10,14,18}_{m20,p20,ramp}.cf32` | 34.8 | 40 | 2439.0 | 27 files. LAP 0xC4ED62, UAP 65. Same grid, ramp -10 kHz. 1031 bursts rendered. |

Non-IQ files: 81 `.json` sidecars and `SHA256SUMS.txt`.

#### `sdr-synth-tmp/recall-confirm/N/`

Block N, the false-positive control: noise only, no bursts of any kind, the same floor and length as blocks A and B (160,880,000 samples). Built by `noise_block` in `scripts/bt_synth_interf.py` at generator_commit fcbc251, written 2026-10-28 20:04 to 20:05. Noise seeds are unused by A and B.

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `rcN_{01..06}.cf32` | 7.7 | 40 | 2439.0 | 6 files. No bursts (`n_bursts` 0). One noise seed per file. |

Non-IQ files: 6 `.json` sidecars and `SHA256SUMS.txt`.

### `sdr-synth-tmp/recall-confirm-private/`

The plan and seeds behind recall-confirm. This is the answer key for bluey's blind test. Nothing is copied from it here, and its files were not read for this entry; the purposes below are from the file names. There are no `.cf32` files. The six files are dated 2026-10-28 07:16, and `rc_neg.py` 20:05.

- `rc_plan.json`, `rc_plan2.json`: the plan files for the recall-confirm blocks.
- `rc_cands.json`: the candidate LAPs.
- `rc_screen.txt`: the screen of candidate LAPs.
- `rc_render.sh`: the render commands.
- `rc_neg.py`: the script for block N.
- `used_laps.txt`: the LAPs earlier sets already used.

### `sdr-synth-tmp/e-controls/`

Controls with no Bluetooth packet in them: a carrier, random-bit GFSK bursts with no access code, and noise, each 6 s at 20 and 40 MS/s; plus two 24 s files of one connection with no role switch (the RSF control). The sidecars' `truth` fields say what each file must not contain, and the notes do not record who asked for them. The 6 s files were made by a scratch script, `ec_carrier.py`, which is not in the repo, on `scripts.bt_synth_interf.noise_block` and `apps.bt_br_frame.gfsk` at generator_commit 0ef2d7b, dated 2026-10-30 00:07 to 00:23. The RSF files are `scripts/bt_synth_endlen.py` kind `np` at the same commit, with a second scratch script (not in the repo) that adds the two stems. The truth file `RSF_truth.json` describes them. See [bluey-test-signals](bluey-test-signals.md) (the NULL/POLL-heavy connection, `E_np`).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `ec_cw_{m100,m58,m30,p30,p58,p100}_40msps.cf32` | 11.5 | 40 | 2439.0 | 6 files. Unmodulated carrier, no bursts. Tone at 2441 MHz (BT channel 39) with offset -100 / -58 / -30 / +30 / +58 / +100 kHz. 20 dB in 1 MHz, amplitude 0.25. |
| `ec_cw_{m100,m58,m30,p30,p58,p100}_20msps.cf32` | 5.8 | 20 | 2439.0 | 6 files. As above at 20 MS/s. |
| `ec_gfsk_{m100,m58,m30,p30,p58,p100}_40msps.cf32` | 11.5 | 40 | 2439.0 | 6 files. Same offsets. Random-bit GFSK bursts, 2870 bits each (DH5 length), every 3750 us, 1599 bursts, no preamble and no sync word. |
| `ec_gfsk_{m100,m58,m30,p30,p58,p100}_20msps.cf32` | 5.8 | 20 | 2439.0 | 6 files. As above at 20 MS/s. |
| `ec_noise_{20,40}msps.cf32` | 2.9 | 20 and 40 | 2439.0 | 2 files. Noise only. |
| `synth_endlen_RSF_np_40msps_s1.cf32` | 7.7 | 40 | 2439.0 | RSF control, 24 s. One connection, LAP 0x037F4F, UAP 0x9A, no role switch, no ID or FHS. Master -30 kHz, slave +30 kHz. 19200 bursts rendered. |
| `synth_endlen_RSF_np_20msps_s1.cf32` | 3.8 | 20 | 2439.0 | As above at 20 MS/s. 14362 bursts rendered. |

Non-IQ files: 28 `.json` sidecars (the RSF two are about 24 MB each), `RSF_truth.json`, and `SHA256SUMS.txt`.

### `sdr-synth-tmp/rxsweep/`

The receiver's own frequency offset applied to two slotb2 files, LAPs 0x0466AB and 0x4073AD: the same bursts with `scripts/bt_rx_offset.py --rx-offset-hz` set, a pure rotation with no ppm and no DC spur. The transmitter's offset stays 0, so the receiver reads the offset alone (`cfo_observed_hz`). Built 2026-10-30 07:46 to 09:09, manifest 09:46, with `bt_rx_offset.py` at tool_commit 616bec9; the notes do not record who asked for the sweep. Sources: `samelap/synth_slotb2_S_0466AB_c0_p10_n1.cf32` (generator_commit 287635c) and `laptest/synth_slotb2_S_4073AD_c0_p10_n1.cf32` (a894e02). See [bluey-test-signals](bluey-test-signals.md) (a receiver's own offset, 2026-10-17).

| File | GB | Rate MS/s | Centre MHz | What differs |
|---|---|---|---|---|
| `synth_slotb2_S_0466AB_c0_p10_n1__rx_off{m100k,m70k,m45k,p45k,p58k,p70k,p100k}.cf32` | 26.9 | 40 | 2439.0 | 7 files. LAP 0x0466AB, UAP 95. Receiver offset -100 / -70 / -45 / +45 / +58 / +70 / +100 kHz. 20 dB. |
| `synth_slotb2_S_4073AD_c0_p10_n1__rx_off{m100k,m70k,m45k,p45k,p58k,p70k,p100k}.cf32` | 26.9 | 40 | 2439.0 | 7 files. LAP 0x4073AD, UAP 206. Same offsets. 20 dB. |

Non-IQ files: 14 `.json` sidecars (about 21 MB each), `manifest.json` (set `rx_offset`, `rx_ppm` 0 in every file), and `SHA256SUMS.txt` covering the 28 files.

## Bench recordings for bluey, 2026-10-18 to 10-27

Recordings made on the BB60D of the VSG60A playing the bench shots for bluey-ox-walker's Bluetooth tests; the Played / recorded column reads shot, level and commanded offset, then seconds, gain, adc and lost overflow counts from the `.rec.json`, and `warn`, the number of `Sample Overrun` lines in the `.rec.log` (the json does not count them).

### `sdr-synth-tmp/bench/attachments/`

Two text files and no IQ: `cal2_raw.snr.json`, the SNR measurement of `cal/cal2_885614.cf32` (18.96 dB, from 2026-10-19), and `test_bt_bench.log`, the output of the `bt_bench.py` test script run on 2026-10-19 at 15:51. Nothing to play or record here.

### `sdr-synth-tmp/bench/cal/`

Two calibration plays of the shot `bench_885614_d23` on 2026-10-19, 14:25 and 16:08. `cal2_885614` was played at -69 dBm for 30 s and is aligned; `cal_885614` was played at -90 dBm for 45 s and is not, because the aligner cannot lock that low (see [bluey-test-signals](bluey-test-signals.md) and [bluetooth](bluetooth.md#the-bench)). Built by bt_synth_slotb2.py at 2abf33a. Non-IQ files: `cal2_885614.rec.log`, `cal2_885614.cf32.rec.json`, `cal2_885614_tx.json`, `cal_885614.rec.log`, `cal_885614.cf32.rec.json`, `cal_885614_tx.json`, `SHA256SUMS.txt`, and the crop sidecar `cal2_align/cal2_885614.json` with `cal2_align/cal2_885614.align.json`.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `cal2_885614.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; crop in cal2_align/ | bench_885614_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `cal_885614.cf32` | 14.4 | 40 | 2439.0 | LAP 0x885614; not aligned: shot not found at -90 dBm (median correlation 0.170, see captures/) | bench_885614_d23, -90 dBm, +0 kHz; 45 s, gain 60 %, adc 0, lost 0, warn 0 |

### `sdr-synth-tmp/bench/captures/`

One noise-only recording, made on 2026-10-19 at 07:39: the VSG60A closed with RF off and the BB60D recording 20 s at 2439.0 MHz, gain 60 %. Non-IQ files: `noise.cf32.rec.json` and `cal_885614.align.json`. The second is the aligner's refusal of `cal/cal_885614.cf32` (its capture is 1,800,000,000 samples, 45 s), kept with the noise file.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `noise.cf32` | 6.4 | 40 | 2439.0 | noise only | not played (VSG off); 20 s, gain 60 %, adc 0, lost 0, warn no log |

### `sdr-synth-tmp/bench/confirm/`

The 2026-10-23 sitting for two of the FR-set LAPs, 0x20C0AA and 0xDBB3FB (listed in [bluey-test-signals](bluey-test-signals.md)), with the shots made at 12:57 that day. `main/` holds 16 scored recordings, r01 to r16, at -68 dBm and offsets of 0, ±35 and ±58 kHz. `raw/` holds four checks at the same level: two 0 kHz calibrations (`cal-`) and two at ±35 kHz (`chk-`), each with its crop in `raw/align/`. `plays/` has the sidecar copies of those four plays and `plays_sums.txt` the hashes; `check_play_record.json` says the four passed and that one throw-away start was played before them. `stray_start_r01/session.jsonl` is the log of a start that is not a recording.

**Odd:** the shots and crops carry generator commit `6eff350-dirty`, so the tree was not clean and the commit does not pin the generator. Crop `align/r15` does not exist: its crop was refused (a 0.50-sample step at shot sample 398092872, "samples lost or gained"), although `r15`'s rec.json says `lost_sample_overflows` 0.

Non-IQ files: `SHA256SUMS.txt`, `session.jsonl`, and per recording `.align.json`, `.align.log`, `.align_allowed.json`, `.cf32.rec.json`, `.rec.log`, `.snr.json` and `_tx.json`; `align/` holds the crop sidecars `rNN.json`.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `r01.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.05 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r02.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.07 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 1 |
| `r03.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.40 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r04.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.59 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r05.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.66 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r06.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.68 dB; crop accepted | bench_20C0AA_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r07.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.77 dB; crop accepted | bench_20C0AA_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r08.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.83 dB; crop accepted | bench_20C0AA_d23, -68 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r09.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.92 dB; crop accepted | bench_20C0AA_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 1 |
| `r10.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.90 dB; crop accepted | bench_20C0AA_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r11.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.68 dB; crop accepted | bench_20C0AA_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r12.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.76 dB; crop accepted | bench_20C0AA_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r13.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.92 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, +35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r14.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.97 dB; crop accepted | bench_DBB3FB_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r15.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.68 dB; crop refused; align: a 0.50-sample step at shot sample 398092872, "samples lost or gained"| bench_DBB3FB_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r16.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.79 dB; crop accepted | bench_20C0AA_d23, -68 dBm, +35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `align/` `r01.cf32` to `r16.cf32`, except `r15.cf32` (15 files) | 3.4 GB each, 51.0 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 6eff350-dirty | the recording of the same name |

Raw checks (`raw/`):

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `cal-20C0AA_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.06 dB; crop in align/ (no accept file) | bench_20C0AA_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `cal-DBB3FB_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.10 dB; crop in align/ (no accept file) | bench_DBB3FB_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `chk-20C0AA-p35_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0x20C0AA; SNR 20.05 dB; crop in align/ (no accept file) | bench_20C0AA_d23, -68 dBm, +35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `chk-DBB3FB-m35_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0xDBB3FB; SNR 20.09 dB; crop in align/ (no accept file) | bench_DBB3FB_d23, -68 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `align/` `cal-20C0AA_1.cf32`, `cal-DBB3FB_1.cf32`, `chk-20C0AA-p35_1.cf32`, `chk-DBB3FB-m35_1.cf32` (4 files) | 3.4 GB each, 13.6 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 6eff350-dirty | the recording of the same name |

### `sdr-synth-tmp/bench/foffset/`

The f-offset bench sitting of 2026-10-27, 05:02 to 06:26, the offset sweep set out in `knowledge/freshset/f-offset-bench-sdr-statements.md` (see also [bluey-test-signals](bluey-test-signals.md)). Two LAPs, 0x13B6FA and 0x66C0A6, played from the shots `bench_13B6FA_d23` and `bench_66C0A6_d23` at commanded offsets from -125 to +125 kHz and at 0 kHz. The order is `order/foffset_play_order_v2.json` (15 slots, in use); `foffset_play_order.json` is the superseded version 1. Each recording's tx sidecar matches its slot. `r00` is slot 0, an unscored start. `r08` is a first take that was refused (SNR 13.96 dB, sem 10.2, 21 overrun warnings, no crop); `r08b` is its retake. Built by bt_synth_slotb2.py at 4bf3af8.

Non-IQ files: `SHA256SUMS.txt`, `session.jsonl`, the per-recording sidecars as in `main/` above, and `align/rNN.json` crop sidecars.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `r00.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.14 dB; crop accepted; unscored start (slot 0)| bench_66C0A6_d23, -69 dBm, +125 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r01.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.27 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +125 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r02.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.11 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -100 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r03.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.27 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +100 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r04.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.15 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r05.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.09 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +100 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r06.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.03 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -125 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r07.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.03 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +125 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r08.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 13.95 dB; no crop; first take, refused at the SNR check (see above)| bench_13B6FA_d23, -69 dBm, +75 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 21 |
| `r08b.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.24 dB; crop accepted; retake of r08| bench_13B6FA_d23, -69 dBm, +75 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 1 |
| `r09.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.94 dB; crop accepted | bench_13B6FA_d23, -69 dBm, -75 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r10.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.01 dB; crop accepted | bench_13B6FA_d23, -69 dBm, -125 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r11.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.95 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -75 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r12.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.64 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +75 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r13.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.07 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r14.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.71 dB; crop accepted | bench_13B6FA_d23, -69 dBm, -100 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `align/` `r00.cf32` to `r14.cf32`, with `r08b.cf32` (15 files) | 3.4 GB each, 51.0 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 4bf3af8 | the recording of the same name |

### `sdr-synth-tmp/bench/frate/`

The F + rate confirmatory sitting, 2026-10-25 21:05 to 2026-10-26 05:53, for the FC pair of LAPs 0x13B6FA and 0x66C0A6 (shots made 2026-10-25 at 18:40). Its statements are in `knowledge/freshset/f-rate-confirm-sdr-statements.md` and the amendment in `f-rate-amendment-2026-10-26.md`. `order/frate_order_table.json` gives the 20 scored slots (r01 to r20); every recording's tx sidecar matches its slot. `r01` and `r02` were discarded at their step, and their retakes `r01b` and `r02b` are the accepted ones. The amendment says the crop of slot 1, r01b, is delivered as `r01.cf32`; on the drive the crop is still named `r01b` and the rNN name is slot-based. Built by bt_synth_slotb2.py at 4bf3af8; the diag set at 2abf33a.

**Odd:** the amendment says `cal-13B6FA_1` aligned 4,142 of 4,948 bursts and was cut part way, and that the shot for `cal-66C0A6_1` was not found; both are spoiled, as their rows say. Their json overflow counts read 0. Several recordings have `Sample Overrun` lines in the log but 0 in the json (see the warn column). The amendment's own finding is that the BB60D's overflow code is never returned, so the json counts cannot show a drop.

Non-IQ files: `order/frate_order_table.json`; `main/SHA256SUMS.txt`, `main/session.jsonl`, and per recording the sidecars as above; `cal/`, `diag/` and `noise/` each have `SHA256SUMS.txt`; `noise/noise.cf32.rec.json` and `noise/noise.rec.log`.

`main/` (22 recordings, the 20 slots and two retakes):

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `r01.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 21.07 dB; no crop; discarded at its step; retake r01b| bench_13B6FA_d23, -68 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 1 |
| `r01b.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.83 dB; crop accepted; retake of r01| bench_13B6FA_d23, -69 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 11 |
| `r02.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 17.77 dB; no crop; discarded at its step (SNR 17.77 dB, sem 2.51); retake r02b| bench_13B6FA_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 14 |
| `r02b.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.22 dB; crop accepted; retake of r02| bench_13B6FA_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r03.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.08 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r04.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.99 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r05.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.67 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r06.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.70 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r07.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.89 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r08.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.96 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r09.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.84 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r10.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.88 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r11.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.08 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r12.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.93 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r13.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.94 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r14.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.11 dB; crop accepted | bench_13B6FA_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r15.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.71 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r16.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.85 dB; crop accepted | bench_66C0A6_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r17.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.88 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -35 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r18.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.05 dB; crop accepted | bench_13B6FA_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r19.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.09 dB; crop accepted | bench_13B6FA_d23, -69 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r20.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 19.93 dB; crop accepted | bench_66C0A6_d23, -69 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `align/` `r01b.cf32` to `r20.cf32` (20 files) | 3.4 GB each, 68.0 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 4bf3af8 | the recording of the same name |

`cal/`, `diag/` and `noise/`:

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `cal-13B6FA_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 19.37 dB; no crop; spoiled: 4,142 of 4,948 bursts aligned, cut part way (amendment)| bench_13B6FA_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 9 |
| `cal-13B6FA_2.cf32` | 9.6 | 40 | 2439.0 | LAP 0x13B6FA; SNR 20.09 dB; crop in align/ (no accept file) | bench_13B6FA_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 12 |
| `cal-66C0A6_1.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 9.70 dB; no crop; spoiled: shot not found (amendment)| bench_66C0A6_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 29 |
| `cal-66C0A6_2.cf32` | 9.6 | 40 | 2439.0 | LAP 0x66C0A6; SNR 20.80 dB; crop in align/ (no accept file) | bench_66C0A6_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `diag1-885614.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.26 dB; crop in align/ (no accept file); repeat 1 of 3 at 0 kHz, diagnostic set| bench_885614_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 20 |
| `diag2-885614.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.69 dB; crop in align/ (no accept file); repeat 2 of 3 at 0 kHz, diagnostic set| bench_885614_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `diag3-885614.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 21.13 dB; crop in align/ (no accept file); repeat 3 of 3 at 0 kHz, diagnostic set| bench_885614_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 5 |
| `align/` `cal-13B6FA_2.cf32`, `cal-66C0A6_2.cf32` (2 files) | 3.4 GB each, 6.8 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 4bf3af8 | the recording of the same name |
| `align/` `diag1-885614.cf32` to `diag3-885614.cf32` (3 files) | 3.4 GB each, 10.2 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 2abf33a | the recording of the same name |
| `noise/noise.cf32` | 9.6 | 40 | 2439.0 | noise only | not played (VSG RF off), 30 s, gain 60 %, adc 0, lost 0, warn 0 |

### `sdr-synth-tmp/bench/main/`

A bench sitting of 2026-10-20, 05:21 to 05:58: 12 scored recordings, r01 to r12, of two FR-set shots, `bench_885614_d23` (0x885614) and `bench_EED5EF_d23` (0xEED5EF), played at -68 dBm with the VSG at 0 and ±58 kHz. Recorded 30 s each at gain 60 %, with no overflow counts and no log warnings; all 12 crops were accepted (`align/`). Built by bt_synth_slotb2.py at 2abf33a. `session.jsonl` is the sitting's log. See [bluey-test-signals](bluey-test-signals.md).

Non-IQ files: `SHA256SUMS.txt`, `session.jsonl`, `rNN.align_allowed.json`, `rNN.align.json`, `rNN.align.log`, `rNN.cf32.rec.json`, `rNN.rec.log`, `rNN.snr.json`, `rNN_tx.json`, and `align/rNN.json` crop sidecars.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `r01.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.32 dB; crop accepted | bench_885614_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r02.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.22 dB; crop accepted | bench_EED5EF_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r03.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.25 dB; crop accepted | bench_EED5EF_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r04.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.29 dB; crop accepted | bench_885614_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r05.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.26 dB; crop accepted | bench_885614_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r06.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.25 dB; crop accepted | bench_885614_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r07.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.16 dB; crop accepted | bench_EED5EF_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r08.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.14 dB; crop accepted | bench_EED5EF_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r09.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.15 dB; crop accepted | bench_885614_d23, -68 dBm, -58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r10.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.13 dB; crop accepted | bench_EED5EF_d23, -68 dBm, +0 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r11.cf32` | 9.6 | 40 | 2439.0 | LAP 0xEED5EF; SNR 20.32 dB; crop accepted | bench_EED5EF_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `r12.cf32` | 9.6 | 40 | 2439.0 | LAP 0x885614; SNR 20.32 dB; crop accepted | bench_885614_d23, -68 dBm, +58 kHz; 30 s, gain 60 %, adc 0, lost 0, warn 0 |
| `align/` `r01.cf32` to `r12.cf32` (12 files) | 3.4 GB each, 40.8 GB in all | 40 | 2439.0 | crops of the recording of the same name (shot plus 0.25 s either side); sidecar `align/<name>.json`; generator 2abf33a | the recording of the same name |

### `sdr-synth-tmp/bench/shots/`

The six 10 s shot files that `bt_bench_play.py` sends from the VSG60A: 400,000,000 samples each, 3.2 GB. Each is `bench_<LAP>_d23`: the d23 cell, with +23 ppm of clock drift, SNR 20.0 dB and no carrier offset, rendered by bt_synth_slotb2.py from the stem named in its sidecar (the `_n` number is the noise seed). The 0x885614 and 0xEED5EF shots are from 2026-10-18 (commit 2abf33a), 0x20C0AA and 0xDBB3FB from 2026-10-23 (commit **6eff350-dirty**), and 0x13B6FA and 0x66C0A6 from 2026-10-25 (commit 4bf3af8). The `_check.json` files for 0x13B6FA and 0x66C0A6 are the player's `--check` runs and say "check: never sent".

Non-IQ files: one `.json` sidecar per shot (about 22 MB each, the generator's full description), and the two `_check.json` files.

| File | GB | Rate MS/s | Centre MHz | What differs | Played / recorded |
|---|---|---|---|---|---|
| `bench_885614_d23.cf32` | 3.2 | 40 | - | LAP 0x885614, UAP 231, stem `F_885614_d23_n3004`, SNR 20.0 dB, drift +23 ppm; generator 2abf33a | source of 11 sent recordings on the drive; made 2026-10-18 |
| `bench_EED5EF_d23.cf32` | 3.2 | 40 | - | LAP 0xEED5EF, UAP 242, stem `F_EED5EF_d23_n3012`, SNR 20.0 dB, drift +23 ppm; generator 2abf33a | source of 6 sent recordings on the drive; made 2026-10-18 |
| `bench_20C0AA_d23.cf32` | 3.2 | 40 | - | LAP 0x20C0AA, UAP 34, stem `F_20C0AA_d23_n3008`, SNR 20.0 dB, drift +23 ppm; generator 6eff350-dirty | source of 11 sent recordings on the drive; made 2026-10-23 |
| `bench_DBB3FB_d23.cf32` | 3.2 | 40 | - | LAP 0xDBB3FB, UAP 167, stem `F_DBB3FB_d23_n3016`, SNR 20.0 dB, drift +23 ppm; generator 6eff350-dirty | source of 10 sent recordings on the drive; made 2026-10-23 |
| `bench_13B6FA_d23.cf32` | 3.2 | 40 | - | LAP 0x13B6FA, UAP 230, stem `F_13B6FA_d23_n3101`, SNR 20.0 dB, drift +23 ppm; generator 4bf3af8 | source of 22 sent recordings on the drive; made 2026-10-25 |
| `bench_66C0A6_d23.cf32` | 3.2 | 40 | - | LAP 0x66C0A6, UAP 234, stem `F_66C0A6_d23_n3102`, SNR 20.0 dB, drift +23 ppm; generator 4bf3af8 | source of 20 sent recordings on the drive; made 2026-10-25 |

## Over-the-air recordings, games and archives

The folders on `/media/user/4TB` that hold over-the-air captures, the RF games, and the archives of work copied from worklaptop1 and the bench. Paths are relative to `/media/user/4TB/`.

### `sdr-archive/stage3-ota-raw-20261004/`

The raw BB60D recordings of the stage 3 over-the-air hopping test, made on 2026-10-04 (file times 23:04 to 23:08). A VSG60A played each train once (one-shot) at 0 dBm, centre 2441.0 MHz, 40 MS/s, GFSK at 1 Msym/s, hopping on the map 31-50. The sidecars name `scripts/bt_synth_hop.py` as the generator, for bluey-ox-walker. All six share LAP `0x9e8b33` and UAP `0x47`. See [bluey-test-signals](bluey-test-signals.md#stage-3-over-the-air-2026-10-04).

| File | GB | Packet | Bursts | Map | What differs |
|---|---|---|---|---|---|
| `m10.cf32` | 7.7 | DH5 | 200 | 31-50 | 24 s recording, the only file here not named `ota_`; no note in bluey-test-signals.md names it |
| `ota_hop_dh5_0dbm.cf32` | 6.4 | DH5 | 200 | 31-50 | 20 s, 0 overflows |
| `ota_hop_dh3_0dbm.cf32` | 6.4 | DH3 | 240 | 31-50 | 20 s, 1 ADC overflow in the info sidecar; six `Sample Overrun` warnings in its log |
| `ota_hop_dh1_0dbm.cf32` | 6.4 | DH1 | 600 | 31-50 | 20 s, 0 overflows |
| `ota_hop_dh1_long_0dbm.cf32` | 6.4 | DH1 | 2400 | 31-50 | the long DH1 train, 20 s, 0 overflows |
| `ota_hop_change_dh5_0dbm.cf32` | 6.4 | DH5 | 240 | 31-50, then 32-51 | the map changes at burst 120 |

Each file has a `*_tx.json` (the truth), a `*.cf32.info.json` (the recorder's numbers) and a `*.rec.log`: 18 non-IQ files in all. Every recording has gain 60 % in its info sidecar.

### `data/iq/` (the 7 `ota_` files only)

Copies made on 2026-10-31 of the stage 2 and stage 3 over-the-air captures. The two stage 2 files are whole 30 s runs on 2441 MHz at 20 MS/s. The five stage 3 files are each the shot plus 1 s on either side, cropped from a 20 s recording at 40 MS/s, centred on 2441 MHz (rates and burst counts from [bluey-test-signals](bluey-test-signals.md#stage-3-over-the-air-2026-10-04)). None of the seven has a sidecar in this folder; the truth sidecars are in bluey's local tree, `/data/python/bluey-ox-walker/data/sidecar/`, under the same stems.

| File | GB | Packet | Bursts | What differs |
|---|---|---|---|---|
| `ota_dh5_0dbm_20261004_070334.cf32` | 4.8 | DH5 | 7875 sent | stage 2 |
| `ota_dh3_0dbm_20261004_070436.cf32` | 4.8 | DH3 | 11999 sent | stage 2 |
| `ota_hop_dh5_0dbm_20261004_230602.cf32` | 0.9 | DH5 | 200 | stage 3, cropped |
| `ota_hop_dh3_0dbm_20261004_230656.cf32` | 0.8 | DH3 | 240 | stage 3, cropped |
| `ota_hop_dh1_0dbm_20261004_230723.cf32` | 0.9 | DH1 | 600 | stage 3, cropped |
| `ota_hop_dh1_long_0dbm_20261004_230746.cf32` | 1.4 | DH1 | 2400 | stage 3, cropped |
| `ota_hop_change_dh5_0dbm_20261004_230809.cf32` | 0.9 | DH5 | 240 | stage 3, cropped; map change at burst 120 |

### `data/synth-batch2/mixed_v2/` compared with `sdr-synth-tmp/mixed/`

Yes, the same set. Both folders hold `synth_mixed_blind_a` and `synth_mixed_blind_b`, built by `scripts/bt_synth_mixed.py` at generator_commit `a74a7e8`. The two `.cf32` files match in size in both folders. The `.cf32` contents were not hashed, so the match is by size only. The two `.json` sidecars are byte-identical (checked with `cmp`). The blind sets are for bluey-ox-walker: 8 unsynchronised piconets on the map 31-50, with truth per burst; see [bluey-test-signals](bluey-test-signals.md) under "Mixed devices for blind tests".

| File | cf32 bytes (both) | sidecar bytes (both) | `mixed_v2` written | `sdr-synth-tmp/mixed` written |
|---|---|---|---|---|
| `synth_mixed_blind_a` | 2,013,265,920 (2.0 GB) | 19,338,109 | 2026-10-31 07:15 | 2026-10-07 05:07 |
| `synth_mixed_blind_b` | 2,013,265,920 (2.0 GB) | 20,268,580 | 2026-10-31 07:16 | 2026-10-07 05:08 |

### `sdr-archive/rf-games-20261030-night2/`

RF games, night 2, 2026-10-30 21:20 to 2026-10-31 03:40, rounds 39 to 90. Every round was played cable only, from worklaptop1's VSG60A into fm-rec2's BB60D. Nothing here is to be radiated. The README asks that the answer key not be shown to a receiver under test.

The answer key is `reveals2.txt`, at `/media/user/4TB/sdr-archive/rf-games-20261030-night2/reveals2.txt`. It is named here and not copied. The README gives the replay and rebuild steps.

| Files | Count | GB | What differs |
|---|---|---|---|
| `rxseries/R39.cf32` to `rxseries/R90.cf32` | 52 | 79.5 | the exact IQ aired in each round; per-round content is in `reveals2.txt` |

Beside the `.cf32` files, `rxseries/` holds 85 `.json` files (app-config rounds and blind, H and S specs), `play.sh` and `play.py` with `play.log`, and one transport stream, `R82.ts` (97 MB). Round numbers run without a gap.

- `specs/` holds the generator specs `R39.json` to `R91.json` (53 files), two flag files (`R47.flag`, `R82.flag`) and eight PNG pictures named for the APT, SSTV and Feld-Hell rounds, source and decoded. R91 has a spec but no `.cf32` in `rxseries/`.
- `report/` holds `index.html`, the morning report.
- `iqset-from-scratch/` holds `cfg/` (17 config JSON files), `media/` (two WAV files, one a music track and one a 1 kHz stereo separation test), `out/` (11 `.cf32` renders, each with a log, a `TRUTH.txt` that was not read here, and a `cfg/` copy of the configs) and `cfg-folders-exact-names.tar`, a 44-entry tar of the config folders that was listed and not extracted.
- The top level also holds `ctf.py` and `gen2.py`, the generators used on the night, and `README.txt`.

### `sdr-synth-tmp/dm35-capture/`

Scripts and a review written for bluey-ox-walker on 2026-10-07 for a DM3/DM5 over-the-air capture with a Pi-side sender and a BB60D recording. Nothing was run on the rig, and the folder holds no IQ. No knowledge note covers it yet. It is not a git repository.

| File | Size | What it is |
|---|---|---|
| `README_dm35.md` | 116 lines, 10.5 KB | The review: what the plan gets right and wrong, the operator checklist, the BB60D side |
| `run_dm35_capture.sh` | 202 lines | The orchestrator, from `run_kc_3dh_pi4send.sh`; has `DRY_RUN` and `PREFLIGHT` modes |
| `dm35_sender.py` | 141 lines | The Pi-side sender, cycling frame sizes 110 and 200 |
| `dm35_verify.py` | 452 lines | The btsnoop checker, with `--selftest` and `--afh-mask` |
| `__pycache__/dm35_verify.cpython-312.pyc` | 35.9 KB | A compiled copy of `dm35_verify.py` |

### `sdr-synth-tmp/rg-archive/`

Files copied from worklaptop1's `~/rg/` on 2026-10-25, before those folders were removed. They existed nowhere else: not in bluey-ox-walker's git history, not on this drive, and not in the local bluey folders. Every other file of those folders was byte-identical to a copy in git or on the drive. `SHA256SUMS.txt` holds the hashes taken on worklaptop1.

The README names seven subfolders and says nothing about what any of them is. It gives no per-folder description, and no knowledge note mentions them.

| Subfolder | Files | Subfolders | GB |
|---|---|---|---|
| `c0-lapfree` | 429 | 84 | 0.49 |
| `s4-carrier-thr` | 1458 | 170 | 1.05 |
| `s4-laptest` | 379 | 82 | 0.32 |
| `s4-partb` | 249 | 46 | 0.06 |
| `s4-partb2` | 709 | 57 | 0.12 |
| `s4-recordkeep` | 1320 | 251 | 0.50 |
| `uap-skip-id` | 1418 | 148 | 3.24 |

The seven folders hold 5962 files, and `SHA256SUMS.txt` has 5962 lines to match. With `README.txt` and `SHA256SUMS.txt` at the top, the folder holds 5964 files in all.

## A real link recorded, 2026-11-08: a mid-link role switch

### `data/` - `dapie_esp32_roleswitch_20261108_031747`

One take, six files, copied from the PC after the run and checked by sha256.
A Raspberry Pi (`daPie`, BlueZ) pinged an ESP32 running a Classic
BluetoothSerial sketch with `l2ping`, then ran `hcitool sr <esp32> slave`, so
the Pi went from central to peripheral and the ESP32 became master. The BB60D
recorded the whole of it. Made by `scripts/bt_capture_roleswitch.sh` (branch
`bt-roleswitch-capture`, commit `0b7558e` when written).

| File | Size | What |
|---|---|---|
| `iq/bb60d_dapie_esp32_roleswitch_20261108_031747.cf32` | 23.36 GB | 2441 MHz, 40 MS/s, 73 s, RF gain 0; 0 Sample Overrun warnings and 0 ADC overflow lines |
| `btsnoop/btmon_dapie_dapie_esp32_roleswitch_20261108_031747.btsnoop` | 1.7 MB | the Pi's HCI side: AFH command, Connect Complete, the Role Change to Peripheral, Disconnect |
| `sidecar/dapie_esp32_roleswitch_20261108_031747.meta` | 666 B | devices, BD_ADDRs, AFH map, scenario |
| `sidecar/....timeline` | 675 B | `iq_start_wall`, `iq_rate_hz`, marker and switch times on the Pi's clock, in the format `freeze_truth.py` reads |
| `sidecar/....recorder.log` | 5 KB | the recorder's own log |
| `sidecar/....l2ping.log` | 292 KB | 0 % loss |

- **Devices:** Pi D8:3A:DD:65:49:35 (LAP 654935, UAP DD); ESP32 04:B2:47:06:0A:C2 (LAP 060AC2, UAP 47). Unpaired, no encryption.
- **Timeline, from `.timeline`:** warm-up 6 s; two 2.00 s l2ping pauses (SIGSTOP/SIGCONT) before the switch; the switch about 36 s in; one more pause after it; the ESP32 ended the link (Remote User Terminated) about 24 s after the switch.
- **AFH:** the narrow host map `00 00 00 ff df ff 07 00 00 00` was sent to the Pi's controller before the link (status 00). That is only the host's input, and it was not the map in force: decoding found the Pi's controller dropped channel 24 (25 channels, 25-36 and 38-50), and the receiver at 2441 MHz sees only channels 26-52, so channel 25 of that map is out of view and the AFH check failed on the view, not on the switch. After the switch the ESP32 hopped on all 79 channels until about 41.0 s, then on AFH with channels 25-49 (bluey-bb `knowledge/roleswitch-check.md`). The second take below was made to fix the view.
- **Clock:** the Pi's clock was about 0.15 s behind the host's, measured and written to the timeline - but do not apply that to IQ time: the markers show IQ time is the Pi's epoch minus a constant, with no host offset.
- **Written to the internal NVMe, then copied here.** A first attempt written straight to this drive lost samples (304 Sample Overrun warnings): over a long write the exFAT drive manages about 15 MB/s, and the recording needs about 80 MB/s. That run was deleted. Record on the NVMe and copy afterwards.

### `data/` - `dapie_esp32_roleswitch_map_20261108_150907`

The second take, made to fix what the first could not show. Same devices, same
traffic, same switch about 36 s in; two changes in `scripts/bt_capture_roleswitch.sh`
(branches `bt-roleswitch-afhmap` and `bt-roleswitch-afhretry`): the BB60D is
centred on **2439 MHz**, which sees channels 24-50 where 2441 MHz saw 26-52 and
missed a channel of each master's map, and the Pi's controller is asked for the
**AFH map in force** (HCI Read AFH Channel Map, `0x05|0x0006`) after each marker,
before and after the switch. Six files, copied from the internal NVMe after the
run and checked by sha256.

| File | Size | What |
|---|---|---|
| `iq/bb60d_dapie_esp32_roleswitch_map_20261108_150907.cf32` | 23.36 GB | 2439 MHz, 40 MS/s, 73 s, RF gain 0; 0 Sample Overrun warnings; one ADC overflow line 18 ms after the recorder started (start-up settling; the next read shows none) |
| `btsnoop/btmon_dapie_dapie_esp32_roleswitch_map_20261108_150907.btsnoop` | 1.9 MB | the Pi's HCI side: AFH command, the five map reads with their replies, Connect Complete, the Role Change to Peripheral, Disconnect |
| `sidecar/dapie_esp32_roleswitch_map_20261108_150907.meta` | 690 B | devices, AFH host map, scenario |
| `sidecar/....timeline` | 1.6 KB | `iq_start_wall`, `iq_rate_hz`, marker and switch times on the Pi's clock, and an `afhmap_*` line per read |
| `sidecar/....recorder.log` | 5 KB | the recorder's own log |
| `sidecar/....l2ping.log` | 318 KB | ends "Connection reset by peer": the ESP32 ended the link, as before |

- **The map in force, from the `afhmap_*` lines (byte 3 onward):** before the switch AFH is enabled and the map is the host's with channel 24 dropped (`FE DF FF 07 ...`), and from the second marker with channel 32 dropped too (`FE DE FF 07 ...`; channel 37 was already out); after the switch AFH is disabled with an all-zero map (the ESP32 hopping on all 79); after the last marker it is enabled again with channels 25-51 all in (`FE FF FF 0F ...`). All five reads were command-complete on the first try.
- **Link:** connected 15:09:18, Role Change to Peripheral 15:09:54, Disconnect "Remote User Terminated" 15:10:18 (the Pi's clock).
- **Decoded by blue3 (bluey-bb `knowledge/roleswitch-check.md`): the role-switch check passes on this take.** The Pi's piconet locks blind on one clock (all its rows), with maps that match the HCI reads exactly (25-36 and 38-50, then also without 32 from about 25.2 s). After the switch the ESP32 hops on all 79 channels (AFH disabled, as read) until about 41.3 s, then on AFH with channels 25-51, the same clock. The role-switch FHS agrees with the ESP32's clock to 0 slots. The ESP32 does not lock blind (channel 51 is outside the 24-50 view; the 79-channel stretch is too short). The page FHS was not decoded here. The traffic is still DM3 only, so the hop model's gap after multi-slot packets is not tested; a 5-slot take would need `PING_ARGS` with a payload of about 600 B.
- **Written to the NVMe, then copied here**, as the first take was; the rehearsal files in the scratchpad were temporary and are gone.

### `data/` - `dapie_esp32_roleswitch_spp_20261108_184230`

The third take, for the one case the first two did not have: the ESP32 as the **new master sending multi-slot
packets**. Same BB60D centre (2439 MHz), rate, 73 s, switch about 36 s in, markers and five AFH map reads as
the second take; the traffic changed (`scripts/bt_capture_roleswitch.sh` with `SPP=1`, branch
`bt-roleswitch-spp`). daPie runs `l2ping -s 600 -f` to the ESP32, and also opens an RFCOMM channel to it
(`scripts/bt_spp_sink.py`, channel scanned: 1); the ESP32, now running `scripts/esp32_spp_sender`, streams 512-byte
chunks over it, about 100 KB/s, 6.11 MB in all, before the switch as slave and after it as master. At each marker both
streams are told to stop (a `P` byte to the sketch) before the pause is stamped, so the pause is silent both ways.
Seven files, copied from the internal NVMe after the run and checked by sha256.

| File | Size | What |
|---|---|---|
| `iq/bb60d_dapie_esp32_roleswitch_spp_20261108_184230.cf32` | 23.36 GB | 2439 MHz, 40 MS/s, 73 s, RF gain 0; 0 Sample Overrun warnings, 0 ADC overflow lines |
| `btsnoop/btmon_dapie_dapie_esp32_roleswitch_spp_20261108_184230.btsnoop` | 8.3 MB | the Pi's HCI side: the five map reads, Connect Complete, pairing and encryption events including a **link key notification**, the one Role Change to Peripheral, the ACL data both ways |
| `sidecar/dapie_esp32_roleswitch_spp_20261108_184230.meta` | 1 KB | devices, traffic, `encryption=YES` |
| `sidecar/....timeline` | 1.7 KB | `iq_start_wall`, `iq_rate_hz`, marker, switch and `afhmap_*` lines on the Pi's clock |
| `sidecar/....recorder.log` | 5 KB | the recorder's own log |
| `sidecar/....l2ping.log` | | 2111 sent, 2111 received, 0 % loss |
| `sidecar/....spp_sink.log` | | RFCOMM bytes per second on the Pi's clock: zero in each marker window |

- **This take is paired and encrypted.** The RFCOMM connect makes the Pi authenticate by Simple Pairing (Just Works) and encrypt the link. The ESP32's SPP server refuses an unauthenticated channel: with the socket's security level at the lowest (SDP) the ESP32 hung up with "Authentication Failure" in two rehearsals. The Pi does not keep the key, but the btsnoop holds it: **never print, quote or send it.** Pairing and encryption leave the hop sequence, access codes and packet headers alone; the payload after the pairing is encrypted.
- **Packet types are whatever the controllers chose**: not checked here, so BR DH5/DM5 versus EDR 2-DH5/3-DH5 is for the decoder to read off the headers.
- **Map reads (the Pi's controller, bytes 3 onward, bit 0 = channel 24):** `FE DF FF 07` after marker 1, `FF 03 FE 07` after marker 2, `FD 93 FD 06` just before the switch (the Pi's map changed more than once before the switch this time); after the switch AFH disabled with an all-zero map; after marker 3 `FE FF FF 0F`.
- **The link lasted to the end**, about 36 s after the switch, with no disconnect in the window; the ESP32 kept streaming through the switch and to the end. Takes 1 and 2 ended about 24 s after the switch, when the ESP32 hung up.
- **The ESP32 now runs `esp32_spp_sender`**, not the earlier plain test sketch. Its original flash is backed up in the scratchpad of the session that flashed it, and the sketch is in the repo.
- **Decoded by blue3 (bluey-bb `knowledge/roleswitch-check.md`, "Take 3"): the role-switch check passes on this take, and the multi-slot case it was made for works.** The Pi's piconet locks blind on one clock for all its rows, and its three blind maps match the HCI reads exactly, both pre-switch changes included. After the switch the ESP32 hops on all 79 channels for 4.2 s (fit 1.000), then on AFH 25-51 at the same clock; the role-switch FHS is 0 slots off. As new master the ESP32 sent 7,695 three-slot packets, and all 7,471 slave replies sit on the packet's start channel; with the multi-slot rule its fit goes from 0.565 to 0.9996. The live lock gets a 79-channel lock 1.01 s after the switch. **Follow-up:** during its 79-channel seconds the ESP32 also sends 466 three-slot packets whose replies follow the 79-channel sequence, so the multi-slot remap must stay off in that mode. Nothing from the link key was extracted or kept.
- **The NVMe originals of this take are deleted** (sha256 of all seven files checked against these copies first, by blue3), so the files here are the only copy.

## On the drive but not ours

These are listed so nobody takes them for ours. Do not tidy them away.

| Folder | Whose | What |
|---|---|---|
| `data/` (except `iq/ota_*` and `synth-batch2/`) | bluey-ox-walker | its own data tree: BB60D captures of real devices from May and June 2026 (`iq/bb60d_*`), btsnoop logs, sidecars, link keys, WAVs |
| `sdr-synth-tmp/_bluey-batch-notes/` | bluey-ox-walker | its `DONE`, `SHA256SUMS` and `VERIFY` notes on our batch 2 and 3 deliveries |
| `sdr-archive/bluey-lab-20261028/` | bluey-ox-walker | its BB60D lab sweep of 2026-10-28: three 10 s segments at 2415, 2441 and 2467 MHz, SigMF, 9.6 GB, with its analysis |
| `sdr-archive/cells/` | the FM receiver project | the LTE and 5G NR captures its cell decoders were built on, 16.7 GB. Holds real network identifiers: keep it out of anything public. See [lte](lte.md) |
| `sdr-archive/sceptre-recordings-2026-09/` | the Sceptre receiver | two `.cdif` and two `.fft` recordings from 2026-09-29/30. The format is in [ptt](ptt.md#the-cdif-format-sceptres-recordings) |
| `Joe_Sceptre/` | the Sceptre receiver | recordings and class notes, the PTT `.cdif` files among them, see [ptt](ptt.md#recordings-on-the-4tb-drive) |
| `media/` | the user | the videos, WAVs and NTSC stills the transmitter apps play; see [media](media.md) |
| `Gary/`, `KNOWLEDGE_BASE/`, `Skinny_AI_Class1/`, `Skinny_AI_Class2/`, `VoIP_fundamentals/` | the user | unrelated to this repository |
