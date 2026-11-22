// FM receiver: a C interface to xdsopl/LDPC's layered decoder (vendored in xdsopl_ldpc/, 0BSD)
// for DVB-T2's 64K codes (EN 302 755 6.1.2, Annex A), loaded by ../iqt2_native.py with ctypes.
//
// Offset min-sum on saturating int8, one codeword per SIMD lane (32 with AVX2, 16 with SSE4.1
// or NEON). A decoder holds state: use one per thread (t2ldpc_new), never one from two threads.
// Built by tools/build-native.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include "xdsopl_ldpc/simd.hh"
#include "xdsopl_ldpc/ldpc.hh"
#include "xdsopl_ldpc/algorithms.hh"
#include "xdsopl_ldpc/layered_decoder.hh"
#include "xdsopl_ldpc/dvb_t2_tables.hh"

#ifdef __AVX2__
static const int SIZEOF_SIMD = 32;
#else
static const int SIZEOF_SIMD = 16;
#endif
typedef int8_t code_type;
static const int FACTOR = 2;           // the offset is FACTOR / 2 = one int8 step
static const int WIDTH = SIZEOF_SIMD / sizeof(code_type);
typedef SIMD<code_type, WIDTH> simd_type;
typedef NormalUpdate<simd_type> update_type;
typedef OffsetMinSumAlgorithm<simd_type, update_type, FACTOR> algorithm_type;
typedef LDPCDecoder<simd_type, algorithm_type> decoder_type;

// rate index as L1-post's PLP_COD: 0..5 = 1/2, 3/5, 2/3, 3/4, 4/5, 5/6
static LDPCInterface *make(int rate)
{
	switch (rate) {
	case 0: return new LDPC<DVB_T2_TABLE_A1>();
	case 1: return new LDPC<DVB_T2_TABLE_A2>();
	case 2: return new LDPC<DVB_T2_TABLE_A3>();
	case 3: return new LDPC<DVB_T2_TABLE_A4>();
	case 4: return new LDPC<DVB_T2_TABLE_A5>();
	case 5: return new LDPC<DVB_T2_TABLE_A6>();
	}
	return nullptr;
}

template <typename TABLE>
static int table_out(int *out, int cap)
{
	int n = 0, row = 0, p = 0;
	for (int g = 0; TABLE::LEN[g]; ++g)
		for (int r = 0; r < TABLE::LEN[g]; ++r, ++row)
			for (int d = 0; d < TABLE::DEG[g]; ++d) {
				if (n + 2 > cap)
					return -1;
				out[n++] = row;
				out[n++] = TABLE::POS[p++];
			}
	return n / 2;
}

struct T2Decoder {
	LDPCInterface *ldpc;
	decoder_type dec;
	simd_type *buf;
	uint8_t *chk;
	int N, K;
};

extern "C" {

int t2ldpc_lanes() { return WIDTH; }

// (row, address) pairs of the parity address table for a rate, -1 if out does not hold them
int t2ldpc_table(int rate, int *out, int cap)
{
	switch (rate) {
	case 0: return table_out<DVB_T2_TABLE_A1>(out, cap);
	case 1: return table_out<DVB_T2_TABLE_A2>(out, cap);
	case 2: return table_out<DVB_T2_TABLE_A3>(out, cap);
	case 3: return table_out<DVB_T2_TABLE_A4>(out, cap);
	case 4: return table_out<DVB_T2_TABLE_A5>(out, cap);
	case 5: return table_out<DVB_T2_TABLE_A6>(out, cap);
	}
	return -1;
}

void *t2ldpc_new(int rate)
{
	LDPCInterface *ldpc = make(rate);
	if (!ldpc)
		return nullptr;
	T2Decoder *d = new T2Decoder();
	d->ldpc = ldpc;
	d->N = ldpc->code_len();
	d->K = ldpc->data_len();
	d->dec.init(ldpc);
	size_t bytes = sizeof(simd_type) * d->N;
	bytes = (bytes + sizeof(simd_type) - 1) / sizeof(simd_type) * sizeof(simd_type);
	d->buf = (simd_type *)aligned_alloc(sizeof(simd_type), bytes);
	d->chk = new uint8_t[d->N - d->K];
	return d;
}

void t2ldpc_free(void *h)
{
	T2Decoder *d = (T2Decoder *)h;
	if (!d)
		return;
	free(d->buf);
	delete[] d->chk;
	delete d->ldpc;
	delete d;
}

// nblocks codewords of N float LLRs (positive means 0, natural order: data then parity) in llr;
// each is clipped to +-clip, times scale, rounded to int8. The decoded bits go to bits
// (nblocks x N, 0 or 1), ok[b] = 1 where every parity check holds. Returns the most
// iterations any batch used (trials where one did not converge).
int t2ldpc_decode(void *h, const float *llr, int nblocks, float clip, float scale, int trials,
                  uint8_t *bits, uint8_t *ok)
{
	T2Decoder *d = (T2Decoder *)h;
	const int N = d->N, K = d->K, R = N - K;
	int most = 0;
	for (int j = 0; j < nblocks; j += WIDTH) {
		int blocks = j + WIDTH > nblocks ? nblocks - j : WIDTH;
		for (int n = 0; n < WIDTH; ++n) {
			if (n < blocks) {
				const float *src = llr + (size_t)(j + n) * N;
				for (int i = 0; i < N; ++i) {
					float v = std::min(std::max(src[i], -clip), clip) * scale;
					v = std::min(std::max(v, -127.f), 127.f);
					reinterpret_cast<code_type *>(d->buf + i)[n] = (code_type)lrintf(v);
				}
			} else {
				// an idle lane: an all-zero codeword, sure of itself, so it never holds the batch
				for (int i = 0; i < N; ++i)
					reinterpret_cast<code_type *>(d->buf + i)[n] = 64;
			}
		}
		int left = d->dec(d->buf, d->buf + K, trials, blocks);
		int used = left < 0 ? trials : trials - left;
		most = std::max(most, used);
		for (int n = 0; n < blocks; ++n) {
			uint8_t *dst = bits + (size_t)(j + n) * N;
			for (int i = 0; i < N; ++i)
				dst[i] = reinterpret_cast<code_type *>(d->buf + i)[n] < 0;
		}
	}
	// every check, per codeword (EN 6.1.2.1: the information bits at the table's addresses,
	// then p_i and p_(i-1), the accumulator)
	for (int b = 0; b < nblocks; ++b) {
		const uint8_t *c = bits + (size_t)b * N;
		memset(d->chk, 0, R);
		d->ldpc->first_bit();
		for (int k = 0; k < K; ++k) {
			int *acc = d->ldpc->acc_pos();
			int deg = d->ldpc->bit_deg();
			if (c[k])
				for (int e = 0; e < deg; ++e)
					d->chk[acc[e]] ^= 1;
			d->ldpc->next_bit();
		}
		int bad = 0;
		for (int i = 0; i < R && !bad; ++i)
			bad = d->chk[i] ^ c[K + i] ^ (i ? c[K + i - 1] : 0);
		ok[b] = !bad;
	}
	return most;
}

}
