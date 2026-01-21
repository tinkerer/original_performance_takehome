# Broadcast Grouping Optimization for Tree Traversal Kernel

## Problem Setup

We have a kernel that processes 256 items through 16 rounds of tree traversal:

```python
for round in range(16):
    for item in range(256):
        node_val = tree[idx[item]]          # LOAD - the bottleneck
        val[item] = hash(val[item] ^ node_val)
        idx[item] = 2 * idx[item] + branch(val[item])
        if idx[item] >= n_nodes: idx[item] = 0  # wrap at tree bottom
```

**The bottleneck**: 256 scattered loads per round at 2 loads/cycle = 128 cycles/round.

## Key Observation: Items Cluster at Same Nodes

Items don't spread uniformly through the tree. They start together and branch apart:

| Round | Unique Nodes | Load Strategy |
|-------|--------------|---------------|
| 0 | 1 | All 256 items at root (node 0) |
| 1 | 2 | ~128 left (node 1), ~128 right (node 2) |
| 2 | 4 | Items at nodes 3,4,5,6 |
| 3 | 8 | Items at nodes 7-14 |
| 4 | 16 | Items at nodes 15-30 |
| 5 | 32 | Items at nodes 31-62 |
| 6 | 63 | Starting to get sparse |
| 7 | 108 | More sparse |
| 8 | 159 | Approaching full spread |
| 9 | 191 | Near maximum spread |
| 10 | 224 | Last level before wrap |
| 11 | 1 | **WRAP!** All back to root |
| 12-15 | 2,4,8,16 | Pattern repeats |

## The Broadcast Optimization

Instead of 256 scattered loads, load each unique node value ONCE and broadcast to all items at that node.

### Round 0: Single Broadcast
```
node_val = LOAD tree[0]     # 1 load
BROADCAST node_val → all 256 items
```
**Savings: 255 loads eliminated**

### Round 1: Two Broadcasts
```
left_items = [items where prev branch was left]    # ~128 items
right_items = [items where prev branch was right]  # ~128 items

node_val_1 = LOAD tree[1]   # 1 load
BROADCAST node_val_1 → left_items

node_val_2 = LOAD tree[2]   # 1 load
BROADCAST node_val_2 → right_items
```
**Savings: 254 loads eliminated**

### Rounds 2-5: Grouped Broadcasts
Same pattern with 4, 8, 16, 32 groups. Each group shares a node value.

### Rounds 6-10: Sparse - Use Indirection
When groups become numerous (>32), switch to:
```
unique_nodes = get_unique(idx[0:256])  # ~100-200 unique
node_cache = {}
for n in unique_nodes:
    node_cache[n] = LOAD tree[n]       # Load each unique once

for item in range(256):
    node_val = node_cache[idx[item]]   # Lookup, no load
    ...
```

### Round 11: Wrap Resets Everything
All items wrap to root. Single broadcast again.

## Total Load Reduction

| Approach | Loads per 16 rounds |
|----------|---------------------|
| Naive (256 × 16) | 4096 |
| With grouping | 839 |
| **Reduction** | **4.9×** |

## Implementation Challenge: Tracking Groups

To use broadcasts, we need to know which items are in each group. Two approaches:

### Approach A: Implicit Tracking (Current)
- After computing branches, items naturally split
- Track group membership using ALU operations
- Route loaded values to correct items via scratch space

### Approach B: Physical Regrouping
- Actually reorder items so same-node items are contiguous
- Enables direct vector broadcasts (vload → vbroadcast pattern)
- **Cost**: Requires permuting 256 items each round
- **Problem**: ISA has no efficient shuffle/permute instruction

## Why Physical Regrouping Doesn't Pay Off

The ISA lacks permute instructions. Regrouping requires:
- 256 address calculations
- 256 scalar stores (128 cycles)
- 256 scalar loads (128 cycles)

**Regrouping cost: ~256+ cycles per round**
**Load savings: ~100 cycles per round for early rounds**

Net: **Worse** with physical regrouping.

## The Optimal Hybrid Strategy

```
ROUND 0:
    1 load + broadcast (trivial)

ROUNDS 1-5:
    - Track group membership in scratch registers
    - Load 2/4/8/16/32 unique node values
    - Use ALU to route values to correct items
    - Hash in parallel (all items independent within round)

ROUNDS 6-10:
    - Build index→node_val mapping
    - Load ~100-200 unique values into cache region
    - Use indirect lookup during hash

ROUND 11:
    - Everyone at 0, single broadcast

ROUNDS 12-15:
    - Same as rounds 1-4
```

## Cycle Budget

| Phase | Cycles |
|-------|--------|
| Rounds 0-5 (broadcast-efficient) | 6 × ~76 = 456 |
| Rounds 6-10 (sparse, ~150 loads each) | 5 × ~90 = 450 |
| Round 11-15 (broadcast-efficient) | 5 × ~76 = 380 |
| Setup/teardown | ~50 |
| **Total** | **~1336** |

This beats the 1363 target by avoiding the naive 128-cycle load bottleneck in early rounds.

## Key Insight

The tree structure creates natural clustering. By exploiting this:
1. Early rounds: Few unique nodes → broadcasts dominate
2. Late rounds: Sparse but we only load unique values once
3. Wrap at round 11: Free reset to optimal state

The 4.9× load reduction translates directly to cycle savings since loads are the bottleneck.
