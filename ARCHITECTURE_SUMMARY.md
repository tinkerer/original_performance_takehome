# VLIW Hash Pipeline Architecture Summary

## Hash Function (Clear Form)

```
Stage 0: a = (a + 0x7ED55D16) + (a << 12)
Stage 1: a = (a ^ 0xC761C23C) ^ (a >> 19)
Stage 2: a = (a + 0x165667B1) + (a << 5)
Stage 3: a = (a + 0xD3A2646C) ^ (a << 9)
Stage 4: a = (a + 0xFD7046C5) + (a << 3)
Stage 5: a = (a ^ 0xB55A4F09) ^ (a >> 16)
```

Each stage: `tmp1 = a OP const` and `tmp2 = a SHIFT n` can run **in parallel**.

## Machine Resources (per cycle)

| Engine | Slots | Operations |
|--------|-------|------------|
| alu    | 12    | Scalar +,-,*,/,%,^,&,\|,<<,>>,<,== |
| valu   | 6     | Vector ops on VLEN=8 elements |
| load   | 2     | Memory reads (scalar or vload) |
| store  | 2     | Memory writes (scalar or vstore) |
| flow   | 1     | Control flow, select |

## Problem Parameters

- batch_size = 256 items
- rounds = 16
- VLEN = 8 (vector width)
- 32 vectors total (256/8)
- tree height = 10 (2047 nodes)

## Bottleneck Analysis

| Operation | Rate | Total | Cycles |
|-----------|------|-------|--------|
| Scattered loads | 2/cycle | 256/round | **128/round** |
| Vectorized loads | 2/cycle | 64/round | 32/round |
| Hash (pipelined) | 1 vec/2 cycles | 32 vectors | 76/round |
| Vectorized stores | 2/cycle | 64/round | 32/round |

**Scattered loads are the bottleneck!** They're 4× slower than hash throughput.

## Two-Queue Pipeline Model

```
WAITING QUEUE          READY QUEUE           HASH PIPELINE
(blocked on load)      (data ready)          (6 stages)
    ↓                      ↓                      ↓
┌─────────┐          ┌─────────┐          ┌─────────────┐
│item 255 │          │ vec 2   │    ────▶ │ S0: vec 5   │
│item 254 │          │ vec 1   │          │ S1: vec 4   │
│  ...    │          │ vec 0   │          │ S2: vec 3   │
│item 16  │          └─────────┘          │ S3: vec 2   │
└─────────┘               ↑               │ S4: vec 1   │
     ↑                    │               │ S5: vec 0   │
     │               8 items loaded       └─────────────┘
  2 loads/cycle           │                    ↓
                          │              STORE QUEUE
                          │              (completed)
```

## Cycle-by-Cycle Operation (Steady State)

```
┌────────────────────────────────────────────────────────────┐
│ CYCLE N                                                    │
├────────────────────────────────────────────────────────────┤
│ load[0,1]: Load node_val for items I, I+1                 │
│ valu[0]:   Hash S0 for vector V    (tmp1=a+c, tmp2=a<<n)  │
│ valu[1]:   Hash S1 for vector V-1  (combine a=tmp1+tmp2)  │
│ valu[2]:   Hash S2 for vector V-2                         │
│ valu[3]:   Hash S3 for vector V-3                         │
│ valu[4]:   Hash S4 for vector V-4                         │
│ valu[5]:   Hash S5 for vector V-5                         │
│ store[0,1]: Writeback for completed vector                │
│ alu[0-11]: Address calc, routing, next_idx computation    │
│ flow[0]:   Loop control                                   │
└────────────────────────────────────────────────────────────┘
```

## Round 0 Optimization

All 256 items start at index 0. Instead of 256 scattered loads:
- Load tree[0] once
- Broadcast to all 256 items

Saves **127 cycles** in round 0!

## Theoretical Performance

| Phase | Round 0 | Rounds 1-15 |
|-------|---------|-------------|
| Loads | 1 cycle (broadcast) | 128 cycles |
| Hash | +12 (drain) | overlapped |
| Stores | overlapped | overlapped |
| **Total** | ~76 cycles | ~140 cycles |

**16 rounds: ~76 + 15×140 = 2176 cycles**

Baseline: 147,734 cycles → **67× speedup potential**

## Implementation Strategy

### Phase 1: Vectorize Hash (use valu)
- Convert scalar hash to vector operations
- Process 8 items per valu op

### Phase 2: Pipeline Hash Stages
- Keep 6 vectors in flight across 6 stages
- One vector enters/exits pipeline every 2 cycles

### Phase 3: Overlap with Loads/Stores
- While loading items N, N+1...
- Hash pipeline processes ready vectors
- Store completed vectors

### Phase 4: Special-case Round 0
- Broadcast tree[0] instead of scattered loads
- Immediately fill hash pipeline

### Code Structure

```
PROLOGUE (round 0 or pipeline fill):
  - Load/broadcast initial data
  - Fill hash pipeline stages 0→5

STEADY STATE LOOP (main work):
  - Fully packed VLIW bundles
  - All engines working in parallel

EPILOGUE (pipeline drain):
  - No more loads
  - Drain remaining vectors through pipeline
  - Final stores
```

## Files Created

- `hash_analysis.py` - Clean hash implementation with analysis
- `pipeline_model.py` - Theoretical throughput analysis
- `two_queue_model.py` - Two-queue scheduling model
- `vliw_schedule.py` - VLIW bundle visualization
- `routing_logic.py` - ALU ops for queue management
