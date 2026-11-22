# xdsopl/LDPC (vendored)

The layered LDPC decoder and the DVB-T2 parity tables from
<https://github.com/xdsopl/LDPC>, commit `32357d8ad55a6a302c34e093759f0454e45cca56`
(2023-05-05), by Ahmet Inan, under the 0BSD licence in `LICENSE` (no conditions).

Only the files `../t2ldpc.cc` needs are here: `simd.hh` with `avx2.hh`, `sse4_1.hh` and
`neon.hh`, `generic.hh`, `algorithms.hh`, `exclusive_reduce.hh`, `ldpc.hh`,
`layered_decoder.hh` and `dvb_t2_tables.hh`.

Changes for this app are marked "FM receiver": one, in `generic.hh` (`std::abs<int16_t>`,
which only libc++ accepts, so GCC builds too).

The tables are checked against ours (`iqt2_tables.LDPC_A`, from EN 302 755 Annex A) by
`tools/tests/test_t2_native.py`.
