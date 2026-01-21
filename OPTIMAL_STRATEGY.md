# Optimal Strategy to Beat 1363 Cycles

## Target: < 1363 cycles (Opus best)
## Our estimate: ~1350 cycles

## Key Optimizations

### 1. Vectorized Hash Pipeline (74 cycles/round)
- 6 valu slots process 6 vectors simultaneously
- 1 vector enters every 2 cycles (throughput-limited)
- 32 vectors × 2 + 12 latency = 74 cycles

### 2. Exploit Index Collisions
Actual unique indices per round (from simulation):
```
R0:   1  R1:   2  R2:   4  R3:   8  R4:  16  R5:  32
R6:  62  R7: 108  R8: 172  R9: 208  R10: 229  R11:  1 (wrap!)
R12:  2  R13:  4  R14:  8  R15: 16
```

### 3. Speculative Preloading During Hash
- First vector finishes at cycle ~10 (with early prediction)
- Start preloading indices for next round immediately
- 64 cycles of overlap = 128 loads hidden

### 4. Early Branch Prediction (Key Optimization!)
Instead of waiting for stage 5 to complete:
```
branch = s4[0] ^ 1 ^ s4[16]
```
- s4[0] equals s1[0] (can compute at cycle 3!)
- s4[16] available at cycle 9 (stage 4 done)
- Saves 2 cycles vs waiting for stage 5

### 5. Round 11 Wrap-Around
All 256 items wrap back to root (idx=0) at round 11:
- Only 1 load needed (broadcast to all)
- Pattern repeats for rounds 11-15

## Timing Breakdown

| Component | Cycles |
|-----------|--------|
| Setup (constants, addresses) | 20 |
| Rounds 0-6, 10-15 (hash-bound) | 12 × 74 = 888 |
| Round 7 (preload 172) | 96 |
| Round 8 (preload 208) | 114 |
| Round 9 (preload 229) | 125 |
| Round 0 initial load | +1 |
| Final stores | 32 |
| **TOTAL** | **~1350** |

## Code Structure

```
PROLOGUE:
  - Load constants (6 hash stage constants)
  - Load initial tree[0] (all items start at root)

FOR EACH ROUND:
  1. XOR val with node_val (overlapped with hash startup)
  2. Hash pipeline (74 cycles):
     - 6 valu slots, 6 stages, 32 vectors
     - Early branch prediction at stage 4
  3. Preload for next round (overlapped with hash):
     - Start at cycle 10 when first branches known
     - Load unique indices only (collision optimization)
  4. Index computation (overlapped with preload):
     - Uses valu slots while load slots do preload

EPILOGUE:
  - Store final idx and val arrays (32 cycles)
```

## Further Optimizations to Explore

1. **Bit 16 early prediction**: Trace s4[16] back through stages
2. **Speculative both-branch loading**: For uncertain predictions
3. **Store overlap**: Start stores before all hashes complete
4. **Setup reduction**: Minimize constant loading overhead

## Critical Dependencies

```
Round R:
  node_val[i] depends on idx[i] from round R-1
  idx[i] = 2*old_idx + (hash_result % 2 == 0 ? 1 : 2)

Early prediction allows:
  hash_result[0] = s1[0] ^ 1 ^ s4[16]
  where s1[0] = s0[0] ^ s0[19] = input[0] ^ s0[19]
```
