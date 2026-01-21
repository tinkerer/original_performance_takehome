# Implementation Plan: Fully Optimized VLIW Kernel

## Goal
Beat 1363 cycles with a fully unrolled, vectorized, pipelined kernel exploiting all optimizations.

## Optimizations to Implement

### 1. Loop Unrolling
- Unroll all 16 rounds explicitly (no loop overhead)
- Unroll 32 vectors per round
- Each round has known structure at compile time

### 2. Broadcast Loading by Round
| Round | Groups | Load Strategy |
|-------|--------|---------------|
| 0 | 1 | Load tree[0], broadcast to all 256 |
| 1 | 2 | Load tree[1], tree[2], broadcast to groups |
| 2 | 4 | Load tree[3-6], broadcast to groups |
| 3 | 8 | Load tree[7-14], broadcast to groups |
| 4 | 16 | Load tree[15-30], broadcast to groups |
| 5 | 32 | Load tree[31-62], broadcast to groups |
| 6-10 | sparse | Load unique nodes into cache, use indirection |
| 11 | 1 | Load tree[0] (wrap!), broadcast to all |
| 12-15 | 2,4,8,16 | Same as rounds 1-4 |

### 3. multiply_add Optimization
Transform stages 0, 2, 4 from 3 ops to 1:
```
Stage 0: a = a * 4097 + 0x7ED55D16    # multiply_add
Stage 1: a = (a ^ C1) ^ (a >> 19)     # regular (XOR + shift + XOR)
Stage 2: a = a * 33 + 0x165667B1      # multiply_add
Stage 3: a = (a + C3) ^ (a << 9)      # regular (add + shift + XOR)
Stage 4: a = a * 9 + 0xFD7046C5       # multiply_add
Stage 5: a = (a ^ C5) ^ (a >> 16)     # regular (XOR + shift + XOR)
```

### 4. Vectorized Pipeline
- VLEN = 8 items per vector
- 32 vectors total (256 items)
- 6 valu slots process 6 vectors simultaneously in hash pipeline
- Throughput: 1 vector every 2 cycles after pipeline fills

### 5. Hash Stage Pipelining
```
Cycle:  0   1   2   3   4   5   6   7   8   9   10  11  12
        ─────────────────────────────────────────────────
valu0:  V0.S0 ─────────────────────────────────────────▶
valu1:      V0.S1 ─────────────────────────────────────▶
valu2:          V0.S2 ─────────────────────────────────▶
valu3:              V0.S3 ─────────────────────────────▶
valu4:                  V0.S4 ─────────────────────────▶
valu5:                      V0.S5 ─────────────────────▶
        ─────────────────────────────────────────────────
              V1 enters    V2 enters    ...
```

### 6. Overlapped Execution
```
┌─────────────────────────────────────────────────────────────┐
│ Per-cycle VLIW bundle (steady state):                       │
├─────────────────────────────────────────────────────────────┤
│ load[0,1]:  Load node values for upcoming vectors           │
│ valu[0]:    Hash stage 0 (multiply_add) for vector V        │
│ valu[1]:    Hash stage 1 for vector V-1                     │
│ valu[2]:    Hash stage 2 (multiply_add) for vector V-2      │
│ valu[3]:    Hash stage 3 for vector V-3                     │
│ valu[4]:    Hash stage 4 (multiply_add) for vector V-4      │
│ valu[5]:    Hash stage 5 for vector V-5                     │
│ store[0,1]: Store completed results                         │
│ alu[0-11]:  Address calc, group routing, index computation  │
└─────────────────────────────────────────────────────────────┘
```

## Code Structure

```python
class OptimizedKernelV2:
    def __init__(self):
        self.instrs = []
        # scratch allocation

    def emit_prologue(self):
        """Load constants, setup broadcast vectors for multiply_add"""
        # Constants: 4097, 33, 9 (multiply factors)
        # Constants: hash stage constants
        # Load tree[0] for round 0

    def emit_round_0(self):
        """All items at node 0 - single broadcast"""
        # 1 load, broadcast to all
        # Fill hash pipeline with 32 vectors

    def emit_round_grouped(self, round_num, num_groups):
        """Rounds 1-5, 11-15: Known group structure"""
        # Load num_groups node values
        # Route to correct vectors based on group membership
        # Pipeline hash computation

    def emit_round_sparse(self, round_num):
        """Rounds 6-10: Many groups, use indirection"""
        # Load unique node values into cache
        # Use indirect lookup for each item
        # Pipeline hash computation

    def emit_round_11(self):
        """Wrap round - all back to node 0"""
        # Same as round 0

    def emit_epilogue(self):
        """Final stores, halt"""

    def build(self):
        self.emit_prologue()
        self.emit_round_0()
        for r in range(1, 6):
            self.emit_round_grouped(r, 2**r)
        for r in range(6, 11):
            self.emit_round_sparse(r)
        self.emit_round_11()  # wrap
        for r in range(12, 16):
            self.emit_round_grouped(r, 2**(r-11))
        self.emit_epilogue()
        return self.instrs
```

## Scratch Space Layout

```
Offset  Name                Size    Purpose
──────────────────────────────────────────────────────
0-6     header_vars         7       rounds, n_nodes, batch_size, etc.
7       tmp                 1       scalar temp
8-15    v_idx               8       current indices vector
16-23   v_val               8       current values vector
24-31   v_node              8       loaded node values
32-39   v_tmp1              8       hash temp 1
40-47   v_tmp2              8       hash temp 2
48-55   v_hash              8       hash result
56-63   v_branch            8       branch decisions
64-71   v_const_4097        8       broadcast 4097
72-79   v_const_33          8       broadcast 33
80-87   v_const_9           8       broadcast 9
88-159  v_hash_consts       72      6 hash constants × 8 lanes
160-415 node_cache          256     cache for sparse rounds
416+    additional temps    ...     as needed
```

## Cycle Budget Estimate

| Phase | Cycles |
|-------|--------|
| Prologue (constants, tree[0]) | 15 |
| Round 0 (1 load, 32 vectors) | 70 |
| Rounds 1-5 (2-32 loads each) | 5 × 72 = 360 |
| Rounds 6-10 (sparse) | 5 × 110 = 550 |
| Round 11 (wrap, 1 load) | 70 |
| Rounds 12-15 (2-16 loads) | 4 × 72 = 288 |
| Epilogue | 10 |
| **Total** | **~1363** |

With multiply_add reducing hash latency:
- Each hash: 6 stages → effectively ~4 stages of latency
- Saves ~2 cycles per vector drain
- **Target: ~1300-1320 cycles**

## Implementation Steps

### Step 1: Scaffold
- Create `optimized_kernel_v2.py`
- Setup scratch allocation
- Emit prologue/epilogue

### Step 2: Round 0
- Single broadcast load
- Vectorized hash pipeline with multiply_add
- Verify against reference

### Step 3: Grouped Rounds (1-5)
- Track group membership
- Multiple broadcast loads
- Route values to correct vectors

### Step 4: Sparse Rounds (6-10)
- Build node cache
- Indirect lookup
- Handle varying group sizes

### Step 5: Wrap and Repeat (11-15)
- Round 11 same as round 0
- Rounds 12-15 same as 1-4

### Step 6: Optimization Pass
- Minimize stalls
- Pack VLIW bundles efficiently
- Overlap loads/stores with compute

## Key Files
- `PIPELINE_MODEL.md` - Conceptual model
- `BROADCAST_GROUPING.md` - Load optimization details
- `expand_grouped.py` - Group simulation
- `problem.py` - ISA reference

## Testing Strategy
1. Run against `reference_kernel2` after each round
2. Use trace output to verify values
3. Check cycle count against estimates
4. Profile VLIW slot utilization
