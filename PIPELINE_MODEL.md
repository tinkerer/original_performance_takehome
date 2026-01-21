# Pipeline Model: Tree Nodes as Hashers

## Mental Model

Think of each tree node as a **hasher** - a processing station that items flow through.

```
                              TREE AS HASHER NETWORK

Level 0:                            [H0]
                                   /    \
Level 1:                       [H1]      [H2]
                              /    \    /    \
Level 2:                   [H3]  [H4] [H5]  [H6]
                           / \   / \  / \   / \
Level 3:                 [H7]...............[H14]
                          :                   :
Level 10:            [H1023]................[H2046]
                          \________  ________/
                                   \/
                              WRAP TO H0
```

## Two Phases

### Phase 1: Rounds 0-10 (2047 Hashers)
- Items traverse the full tree depth
- At round 10, items are at level 10 (nodes 1023-2046)
- After hashing, they compute `2*idx + branch` which exceeds tree size
- **All items wrap to node 0**

### Phase 2: Rounds 11-15 (31 Hashers)
- Items start fresh at root
- Only traverse 5 levels before kernel ends
- Uses nodes 0-30 (1+2+4+8+16 = 31 hashers)

```
ROUND:  0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
        │   │   │   │   │   │   │   │   │   │   │   │   │   │   │   │
        └───┴───┴───┴───┴───┴───┴───┴───┴───┴───┴───┘   └───┴───┴───┴───┘
              2047 hashers (full tree)                    31 hashers (top 5 levels)
                    │                                           │
                    └──────────── WRAP AT ROUND 10 ─────────────┘
```

## What Happens at Each Hasher

```python
def hasher(node_val, items_at_this_node):
    """
    Each hasher (tree node) does this:
    1. XOR its node_val with each item's current value
    2. Run the hash function
    3. Compute branch direction (left or right child)
    4. Send item to next hasher
    """
    for item in items_at_this_node:
        item.val = hash(item.val ^ node_val)
        branch = 1 if (item.val % 2 == 0) else 2
        next_node = 2 * current_node + branch
        send_to_hasher(next_node, item)
```

## Pipeline Execution

Items don't wait for each other. As soon as one item finishes at a hasher, it moves to the next.

```
TIME →
      ┌──────────────────────────────────────────────────────────────┐
      │                        CYCLE TIMELINE                         │
      ├──────────────────────────────────────────────────────────────┤
      │ Cycle 0     : Item[0] enters H0                              │
      │ Cycle 1-12  : Item[0] hashing at H0 (6 stages × 2 cycles)    │
      │ Cycle 12    : Item[0] exits H0 → enters H1 or H2             │
      │              : Item[1] can now use H0's cached node_val      │
      │ Cycle 13-24 : Item[0] hashing at H1, Item[1] hashing at H0   │
      │ Cycle 24    : Item[0] exits H1 → enters H3/H4/H5/H6          │
      │              : Item[1] exits H0 → enters H1/H2               │
      │ ...                                                          │
      └──────────────────────────────────────────────────────────────┘
```

## Broadcast Grouping Within Pipeline

**Key insight**: Multiple items at the same hasher can share the node_val load.

```
ROUND 0: All 256 items at H0
┌────────────────────────────────────────────────────────────────────┐
│  H0 receives: item[0], item[1], item[2], ... item[255]            │
│                                                                    │
│  Step 1: LOAD node_val[0] once                    (1 cycle)       │
│  Step 2: BROADCAST to all 256 items               (1 cycle)       │
│  Step 3: XOR val[i] ^ node_val for all i          (1 valu cycle)  │
│  Step 4: Hash all 256 in parallel pipeline        (12 cycles)     │
│  Step 5: Compute branches, send to H1 or H2       (2 cycles)      │
│                                                                    │
│  Items split: ~128 → H1, ~128 → H2                                │
└────────────────────────────────────────────────────────────────────┘

ROUND 1: Items at H1 and H2
┌────────────────────────────────────────────────────────────────────┐
│  H1 receives: ~128 items (those that branched left)               │
│  H2 receives: ~128 items (those that branched right)              │
│                                                                    │
│  PARALLEL EXECUTION:                                               │
│  ┌─────────────────────────┐  ┌─────────────────────────┐         │
│  │ H1: LOAD node_val[1]    │  │ H2: LOAD node_val[2]    │         │
│  │     BROADCAST to ~128   │  │     BROADCAST to ~128   │         │
│  │     XOR + hash + branch │  │     XOR + hash + branch │         │
│  └─────────────────────────┘  └─────────────────────────┘         │
│                                                                    │
│  Items split into 4 groups → H3, H4, H5, H6                       │
└────────────────────────────────────────────────────────────────────┘
```

## Group Evolution Through Rounds

```
Round 0:  [256]                                    1 group (1 load)
             │
Round 1:  [128] [128]                              2 groups (2 loads)
            │     │
Round 2:  [64][64][64][64]                         4 groups (4 loads)
            │  │  │  │
Round 3:  [32]×8                                   8 groups (8 loads)
            │
Round 4:  [16]×16                                 16 groups (16 loads)
            │
Round 5:  [8]×32                                  32 groups (32 loads)
            │
Round 6:  [4]×~64                                 ~64 groups (sparse)
            │
Round 7:  [2-3]×~108                              ~108 groups (sparse)
            │
Round 8:  [1-2]×~159                              ~159 groups (sparse)
            │
Round 9:  [1-2]×~191                              ~191 groups (sparse)
            │
Round 10: [1]×~224                                ~224 groups (sparse)
            │
            └──────── ALL WRAP TO NODE 0 ────────┐
                                                  │
Round 11: [256]                                    1 group (1 load)
            │
Round 12: [128] [128]                              2 groups (2 loads)
            │
...pattern repeats...
```

## Optimal Load Strategy Per Round

| Round | Groups | Strategy | Loads |
|-------|--------|----------|-------|
| 0 | 1 | Single broadcast | 1 |
| 1 | 2 | 2 broadcasts | 2 |
| 2 | 4 | 4 broadcasts | 4 |
| 3 | 8 | 8 broadcasts | 8 |
| 4 | 16 | 16 broadcasts | 16 |
| 5 | 32 | 32 broadcasts | 32 |
| 6-10 | 63-224 | Load unique, use cache | 63+108+159+191+224 = 745 |
| 11 | 1 | Single broadcast (wrap!) | 1 |
| 12 | 2 | 2 broadcasts | 2 |
| 13 | 4 | 4 broadcasts | 4 |
| 14 | 8 | 8 broadcasts | 8 |
| 15 | 16 | 16 broadcasts | 16 |
| **Total** | | | **839** |

vs **4096** loads with naive approach = **4.9× reduction**

## Pipelining the Hash Stages

Within each hasher, the hash function has 6 stages:

```
Hash Pipeline (6 stages, 2 cycles each):
┌─────────────────────────────────────────────────────────────┐
│ Stage 0: a = (a + 0x7ED55D16) + (a << 12)                  │
│ Stage 1: a = (a ^ 0xC761C23C) ^ (a >> 19)                  │
│ Stage 2: a = (a + 0x165667B1) + (a << 5)                   │
│ Stage 3: a = (a + 0xD3A2646C) ^ (a << 9)                   │
│ Stage 4: a = (a + 0xFD7046C5) + (a << 3)                   │
│ Stage 5: a = (a ^ 0xB55A4F09) ^ (a >> 16)                  │
└─────────────────────────────────────────────────────────────┘
```

With 6 valu slots, we pipeline vectors through these stages:

```
Cycle:    0    1    2    3    4    5    6    7    8   ...
          ─────────────────────────────────────────────
valu[0]:  S0   S0   S0   S0   S0   S0   S0   ...
valu[1]:       S1   S1   S1   S1   S1   S1   ...
valu[2]:            S2   S2   S2   S2   S2   ...
valu[3]:                 S3   S3   S3   S3   ...
valu[4]:                      S4   S4   S4   ...
valu[5]:                           S5   S5   ...
          ─────────────────────────────────────────────
Vector:   V0   V0   V0   V0   V0   V0   V0   V1   V1  ...
               V1   V1   V1   V1   V1   V1   V1  ...
                    V2   V2   V2   V2   V2   V2  ...
```

**Throughput**: 1 vector per 2 cycles (after pipeline fills)
**32 vectors × 2 cycles = 64 cycles** for hash pipeline per round

## Full Round Cycle Budget

```
Per Round (with grouping):
┌─────────────────────────────────────────────────────────────┐
│ Phase              │ Early Rounds    │ Sparse Rounds        │
│                    │ (0-5, 11-15)    │ (6-10)              │
├────────────────────┼─────────────────┼─────────────────────┤
│ Load unique nodes  │ 1-32 loads      │ 63-224 loads        │
│                    │ (~16 cycles)    │ (~112 cycles)       │
│                    │                 │                      │
│ Hash pipeline      │ 64 cycles       │ 64 cycles           │
│ (32 vectors)       │                 │                      │
│                    │                 │                      │
│ Store results      │ overlapped      │ overlapped          │
├────────────────────┼─────────────────┼─────────────────────┤
│ Total              │ ~70 cycles      │ ~120 cycles         │
└─────────────────────────────────────────────────────────────┘
```

## Total Cycle Estimate

| Phase | Rounds | Cycles |
|-------|--------|--------|
| Setup (constants, addresses) | - | 20 |
| Early rounds (0-5) | 6 | 6 × 70 = 420 |
| Sparse rounds (6-10) | 5 | 5 × 120 = 600 |
| Early rounds after wrap (11-15) | 5 | 5 × 70 = 350 |
| Teardown | - | 10 |
| **Total** | | **~1400** |

With additional optimizations (early branch prediction, speculative preload):
**Target: ~1350 cycles**

## Summary

1. **Tree nodes are hashers** - processing stations items flow through
2. **Items pipeline** - don't wait for each other between hashers
3. **Grouping enables broadcast** - items at same hasher share the load
4. **Wrap at round 10** - free reset, pattern repeats
5. **2047 + 31 hashers** - first 11 rounds use full tree, last 5 use top levels
6. **4.9× load reduction** - from 4096 to 839 loads total
