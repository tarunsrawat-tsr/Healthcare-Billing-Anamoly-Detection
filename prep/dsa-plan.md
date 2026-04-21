# 28-day DSA plan — C++, product-company SDE rounds

Built from your answers: **~1 month to rounds · never rated on Codeforces (CP 2 years
ago) · 3–4 h/day · product-based companies (Amazon/Google/Microsoft/Adobe/Atlassian
style) · C++.**

Read §1 and §2 first — they explain *why* the plan looks like this. The schedule is
§5. If you only remember one thing: **§4, the daily block, is non-negotiable; the
problem lists are adjustable.**

---

## 1. Where you actually are (and why this isn't a beginner plan)

Two years of CP, never rated, is a specific profile:

**What you have:** you've seen a segment tree or a DFS before, you know what
`O(n log n)` means, you can read a problem statement without panicking, and you've
written C++ under time pressure. That's real. It's why this plan starts at
*patterns* rather than at "what is an array".

**What you don't have:** the reflex. Never being rated means you never repeatedly
solved problems slightly above your level with a clock running and no one to ask.
So the gap isn't knowledge, it's **retrieval speed** — seeing `Longest Substring
Without Repeating Characters` and knowing in 20 seconds that it's a sliding window
with a hashmap of last-seen positions. That comes only from volume plus spaced
repetition, which is what the next 28 days are for.

**The consequence for the plan:** we go wide-then-deep on the ~15 patterns that
cover nearly all interview questions, we revisit the ones you fail, and we spend
zero time on CP-only material (segment trees, Fenwick, suffix automata, FFT,
combinatorics tricks). Those are genuinely impressive and genuinely useless for
the rounds you're taking. If you have spare energy at the end, §9 says where to
put it.

**And the honest constraint:** one month at 3–4 h/day is roughly 100 hours. That
buys about **150 problems done properly** — not 400 done badly. This plan lists
~155. Do not add more. The failure mode here isn't doing too little, it's doing
too much and never revisiting anything.

---

## 2. Strategy: five decisions, and the reasoning

**1. LeetCode-shaped, not Codeforces-shaped.** Product-company rounds ask
"recognise the pattern, write clean code, explain it aloud, state complexity".
CF asks "find the non-obvious reduction in 40 minutes". Different muscle. Your
target is the first one, so your practice should look like the first one. One
timed contest per week (§5, Sundays) keeps the clock-pressure muscle alive
without letting CF taste dominate.

**2. Patterns before topics.** "Trees" is a topic; "tree problems are almost
always either a DFS that returns aggregated information upward, or a BFS by
levels" is a pattern. You'll be asked to *produce* the pattern on demand, so
that's how the days are organised — and each day opens with the pattern
statement, not a definition.

**3. The 25-minute rule.** On a new problem: 25 minutes of genuine attempt (for
hard: 35). Then look at the **approach only** — never the code. Close it, write
the code yourself. If you still can't, read the solution, close it, and put the
problem on the redo list (§6). Reading code and reproducing it feels like
learning and is not.

**4. Redo > new.** A problem you failed and later solved from a blank file is
worth about three problems you solved first try. The plan schedules redo slots
every single day; treat them as the main event, not the warm-up.

**5. Speak from day one.** You'll be interviewed by a human who is grading your
*narration* as much as your code. From day 3, say your approach out loud before
typing, every time, even alone. This feels ridiculous for about two days and then
it's worth more than any extra problem. §7 has the exact 5-part structure.

---

## 3. Resources (pick one per category, don't collect)

| Need | Use | Why this one |
|---|---|---|
| Problem sequence + video explanations | **NeetCode 150** (neetcode.io) | Free, pattern-organised, matches this plan's ordering almost exactly. Videos are short and approach-first. |
| Alternative/backup sheet | **Striver's A2Z DSA sheet** (takeuforward.org) | Free, very popular in Indian campus prep, more exhaustive. Use it if you want more problems on a given day, *not* as a second plan. |
| C++ STL refresh | cppreference + one 40-min STL walkthrough video (Striver's is fine) | You need `unordered_map`, `priority_queue`, `set`, `sort` with custom comparator, and iterators to be automatic. |
| Advanced reference (only if asked) | CP-Algorithms | Graph shortest paths, union-find, trie — deeper than you need, good for one-off lookups. |
| Timed pressure | LeetCode **Weekly Contest** (Sundays) + Biweekly | Free, 4 problems in 90 minutes. Your OA rehearsal. |
| Mocks | A classmate + **me** (§7) | Peer mocks are free and you should do at least two. I can run a live mock in this chat any day, playing interviewer and scoring your answers. |

Do not buy a course. Everything above is free and sufficient.

---

## 4. The daily block (3.5 h — protect this like a class)

| Time | What | Rules |
|---|---|---|
| **0:00–0:20** | **Redo slot.** 1 problem from your redo list, blank file, no notes. | If you solve it clean, it leaves the list forever. If not, it stays and comes back in 3 days. |
| **0:20–0:45** | **Pattern study.** Read/watch the day's pattern. Write the template code from memory *once*. | One page of notes per pattern. By day 28 you'll have a 15-page handwritten book that is yours. |
| **0:45–2:45** | **New problems** (4–7 today, per §5). 25 min each, then approach-only hint. | Narrate out loud from problem 3 onward. Track time and hint count. |
| **2:45–3:10** | **Write-up.** For every problem you *didn't* solve first try: one sentence on the pattern, one on what you missed. Add to redo list. | This is the highest-leverage 25 minutes of the day. Skipping it is how people do 300 problems and improve slowly. |
| **3:10–3:30** | **Spaced recall.** Look at yesterday's write-ups. Name the pattern for each problem you did last week *without* solving it. | Retrieval practice. Cheap, and it's what converts solving into recognition. |

**Track it.** One spreadsheet, five columns: `problem | date | solved-first-try? | minutes | hint-count`. Plus a `redo-date`. In 10 minutes a week you'll be able to see
your real weak pattern instead of guessing — and by day 14 the median minutes for a
medium is the single most useful number you own.

**Where CS fundamentals fit:** from Week 2, take 30 minutes from the *end* of the
block (replace the recall slot twice a week) for OS/DBMS/OOP/CN from the
checklist I'll write next. Do not add it on top — 4.5 h/day for a month is how
you burn out in week three.

---

## 5. The 28 days

Each day opens with the pattern stated in one sentence — say it out loud before you
start, because the skill is producing that sentence on demand, not recognising it
here. Within a day the problems run easy → hard and the last one is often a
stretch. **If a day goes badly, cut the last problem, never the write-up.**

### Week 1 — reflexes on linear patterns

| Day | Pattern (say it out loud before you start) | Problems |
|---|---|---|
| **1** | **Diagnostic + STL.** Not a study day: measure yourself, then make C++ automatic. | Timed diagnostic, 60 min, no hints: Two Sum (1), Valid Parentheses (20), Reverse Linked List (206), Binary Search (704), Climbing Stairs (70), Number of Islands (200). Then 90 min STL: `vector/unordered_map/set/priority_queue/sort+comparator`, and re-solve Two Sum three ways (brute, sort+two-pointer, hash) stating complexity each time. |
| **2** | **Hashing.** "Can I trade space for a lookup?" is the first question for any array problem. | Contains Duplicate (217), Valid Anagram (242), Two Sum (1), Group Anagrams (49), Top K Frequent Elements (347) |
| **3** | **Prefix sums + running state.** "Subarray sum" ⇒ prefix sum + hashmap of counts. | Maximum Subarray (53), Best Time to Buy and Sell Stock (121), Subarray Sum Equals K (560), Product of Array Except Self (238), Majority Element (169) |
| **4** | **Two pointers.** Sorted (or sortable) + a monotone decision ⇒ two pointers, not nested loops. | Valid Palindrome (125), Move Zeroes (283), Container With Most Water (11), 3Sum (15), Sort Colors (75) |
| **5** | **Sliding window.** "Longest/shortest substring or subarray satisfying X" ⇒ expand right, shrink left. | Longest Substring Without Repeating Characters (3), Permutation in String (567), Fruit Into Baskets (904), Longest Repeating Character Replacement (424), Minimum Window Substring (76) |
| **6** | **Stack, incl. monotonic.** "Next greater/smaller" ⇒ monotonic stack, one pass, O(n). | Min Stack (155), Evaluate Reverse Polish Notation (150), Daily Temperatures (739), Next Greater Element I (496), Trapping Rain Water (42) |
| **7** | **Consolidate.** Mixed set + clock. | **LeetCode Weekly Contest** (90 min, all 4). Then 45 min: redo-list clearance. Then 45 min write-up of every contest problem you didn't solve, including the ones you solved slowly. |

**Day 7 checkpoint:** the contest's first two problems should be solved. If Q1+Q2
took more than ~40 minutes combined, Week 2 stays as written but drop every
"stretch" problem — depth on easy/medium beats exposure to hard.

### Week 2 — search and non-linear structures

| Day | Pattern | Problems |
|---|---|---|
| **8** | **Binary search on an index.** The bug is always the boundary: use `lo < hi` with `hi = n-1`, or `lo <= hi` with `hi = n`, and *never mix them*. | Binary Search (704), Search Insert Position (35), Search a 2D Matrix (74), Find First and Last Position (34), Search in Rotated Sorted Array (33) |
| **9** | **Binary search on the answer.** "Minimise the maximum / maximise the minimum" ⇒ guess a value, check feasibility greedily. This pattern is heavily asked and rarely taught in college. | Find Minimum in Rotated Sorted Array (153), Find Peak Element (162), Koko Eating Bananas (875), Capacity to Ship Packages Within D Days (1011), Search a 2D Matrix II (240), Median of Two Sorted Arrays (4, stretch) |
| **10** | **Linked lists.** Pointer surgery: draw it, use a dummy head, and never lose `next` before reassigning. | Merge Two Sorted Lists (21), Linked List Cycle (141), Remove Nth Node From End (19), Reorder List (143), LRU Cache (146) |
| **11** | **Trees I — DFS that returns information upward.** Almost every tree problem is: compute something in the left subtree, compute it in the right, combine at this node. | Maximum Depth (104), Invert Binary Tree (226), Same Tree (100), Diameter of Binary Tree (543), Subtree of Another Tree (572), Binary Tree Maximum Path Sum (124, stretch) |
| **12** | **Trees II — BFS by levels, and BST in-order.** Level order ⇒ queue with a size snapshot. BST ⇒ in-order is sorted; use it instead of re-searching. | Level Order Traversal (102), Right Side View (199), Validate BST (98), Kth Smallest in a BST (230), Lowest Common Ancestor of a BST (235), Lowest Common Ancestor (236) |
| **13** | **Heaps.** "K-th / top-K / median" ⇒ heap. Two heaps for a running median. Say out loud why a heap beats sorting (you don't need full order). | Kth Largest Element in an Array (215), Last Stone Weight (1046), K Closest Points to Origin (973), Task Scheduler (621), Find Median from Data Stream (295), Merge k Sorted Lists (23, stretch) |
| **14** | **Consolidate + first mock.** | Weekly Contest (90 min). Then a **45-minute mock** (§7 — peer or me). Then 45 min redo list. Write down, in one line each, the three patterns you were slowest on this week. |

**Day 14 checkpoint (the important one):** take any medium you have *not* seen from
days 2–13's patterns. You should reach a correct approach in **under 10 minutes**
and working code in **under 35**. If yes, you're on schedule for a product-company
medium. If not, Week 3's stretch problems get cut and the redo list becomes the
main workload.

### Week 3 — graphs, backtracking, greedy

| Day | Pattern | Problems |
|---|---|---|
| **15** | **Graphs I — traversal and topological order.** Grid ⇒ implicit graph, DFS/BFS from each unvisited cell. "Prerequisites / ordering" ⇒ topological sort (Kahn's with an in-degree queue is easier to write correctly than DFS-based). | Number of Islands (200), Clone Graph (133), Rotting Oranges (994), Course Schedule (207), Course Schedule II (210), Pacific Atlantic Water Flow (417) |
| **16** | **Graphs II — union-find and weighted shortest paths.** "Connected components / are these linked / redundant edge" ⇒ union-find. Non-negative weighted shortest path ⇒ Dijkstra with a min-heap. | Number of Provinces (547), Redundant Connection (684), Accounts Merge (721), Network Delay Time (743), Shortest Path in Binary Matrix (1091), Word Ladder (127, stretch). For bounded relaxation — "shortest path with at most k stops" — Cheapest Flights Within K Stops (787) is the classic; do it instead of 127 if you prefer. |
| **17** | **Backtracking I — choose, recurse, un-choose.** Every subset/permutation/combination problem is the same three lines. The skill is knowing what to add to the state and when to prune. | Subsets (78), Subsets II (90), Permutations (46), Combination Sum (39), Generate Parentheses (22), Letter Case Permutation (784) |
| **18** | **Backtracking II + Trie.** Pruning is what separates a passing solution from a TLE. Trie is a 20-line struct that makes prefix problems trivial. | Palindrome Partitioning (131), Word Search (79), Combination Sum II (40), Implement Trie (208), Design Add and Search Words (211) |
| **19** | **Greedy + intervals.** Intervals: sort by start *or* by end, and know which — "merge/overlap" sorts by start, "non-overlapping count / scheduling" sorts by end. Greedy is only correct if you can say the exchange argument in one sentence; if you can't, it's DP. | Merge Intervals (56), Non-overlapping Intervals (435), Minimum Number of Arrows to Burst Balloons (452), Jump Game (55), Jump Game II (45), Gas Station (134), Partition Labels (763) |
| **20** | **Matrix, simulation, design.** Design questions (LRU, median stream, time-based KV) are favourites at Amazon and Adobe: state the data structures *and* the complexity of every operation before writing anything. | Rotate Image (48), Spiral Matrix (54), Set Matrix Zeroes (73), Time Based Key-Value Store (981), LRU Cache (146) again from a blank file |
| **21** | **Consolidate + second mock.** | Weekly Contest (90 min). **60-minute mock** (§7). 45 min redo list. Then write your one-page **pattern cheat sheet**: 15 patterns, one line each, with the trigger phrase. |

### Week 4 — DP, company-specific, and mocks

DP gets four days because it's the pattern with the worst return-per-hour *and* the
one most often asked as the "differentiator" question. The method every day: define
the state in one sentence, write the recurrence, then decide memoisation vs
tabulation. Never start with the table.

| Day | Pattern | Problems |
|---|---|---|
| **22** | **DP I — 1-D state.** `dp[i]` = answer for the prefix ending at `i`. The whole skill is defining `dp[i]` precisely. | Climbing Stairs (70), House Robber (198), Maximum Subarray (53) again as DP, Coin Change (322), Word Break (139), Decode Ways (91) |
| **23** | **DP II — grids and knapsack.** Two dimensions usually means `dp[i][j]` over two prefixes or a grid cell. 0/1 knapsack ⇒ iterate items, capacity *descending* in the 1-D optimisation. | Unique Paths (62), Minimum Path Sum (64), Triangle (120), Partition Equal Subset Sum (416), Target Sum (494), Longest Increasing Subsequence (300) |
| **24** | **DP III — string DP.** `dp[i][j]` over two strings. Write the match/mismatch cases before you optimise space. | Longest Common Subsequence (1143), Edit Distance (72), Longest Palindromic Substring (5), Palindromic Substrings (647), Longest Palindromic Subsequence (516), Maximum Product Subarray (152) |
| **25** | **DP IV + bits.** Bit manipulation is rare but cheap: 30 minutes buys you 5 easy problems. | House Robber II (213), Best Time to Buy and Sell Stock II (122), Coin Change II (518) if time, then bits: Single Number (136), Number of 1 Bits (191), Counting Bits (338), Missing Number (268), Sum of Two Integers (371) |
| **26** | **Company-specific + behavioural.** A typical Amazon OA is 2 coding problems (~70 min), and many versions add a debug-the-code section plus a work-simulation / Leadership-Principles section — the LP part is *scored*, and people fail it by preparing only the coding. Write **three STAR stories from claimsight** (§8). | Two Amazon-tagged mediums from your weak pattern + two hard-tagged problems you'd realistically see: Trapping Rain Water (42) and Merge k Sorted Lists (23), both from scratch. Then 60 min on LP/STAR stories. |
| **27** | **Mock day.** Two full mocks, 45–60 min each, back to back. | Morning: mock with a peer (they read a problem, you narrate and code, they interrupt with "what's the complexity?"). Evening: mock with me, including one problem from a pattern you hate. Then 30 min: redo list, final clearance. |
| **28** | **Taper.** No new problems. | 45 min: read your cheat sheet and your write-ups, re-solve the 3 problems you failed most often. 30 min: rehearse "walk me through your project" out loud (§8). Then stop. Sleep matters more than one more problem. |

**Total: ~155 problems + 4 contests + 4–5 mocks.**

---

## 6. The redo-list protocol (the part people skip)

A problem goes on the list when: you needed a hint, you read any part of a
solution, you got it right but took more than 40 minutes, or you got a wrong
answer after starting to code.

| Interval | What happens |
|---|---|
| **+1 day** | Re-solve from a blank file during the redo slot. |
| **+3 days** | If you failed the +1 redo, or if it took over 20 minutes at +1. |
| **+7 days** | Final check. Solve it clean here and it's off the list permanently. |

Keep the list short by being honest — 15–25 live items is the working range. If it
passes 40, you're attempting problems too hard for today's level: drop to easier
ones in the same pattern rather than letting the list grow.

Once a week (Sunday), look at the list grouped by **pattern**, not by date. If 6 of
your 15 items are DP, that's not a coincidence and Week 4 needs to start earlier —
swap a Week 3 stretch day for a DP day.

---

## 7. Interview execution (practise this, don't just read it)

**The 5-part structure, every problem, every time:**

1. **Clarify (30–60 s).** Ask 2–3 real questions: input size and range (this
   decides whether O(n²) is acceptable), duplicates allowed, sorted or not, what
   to return on no-answer, can the input be empty. Interviewers grade this.
2. **Example.** Restate the problem with one concrete small example and walk it.
   If you can't, you don't understand it yet.
3. **Approach + complexity, before any code.** "Brute force is O(n²) by checking
   every pair. I can do O(n) with a hashmap of complements, at O(n) space." Then
   wait — they may redirect you, and being redirected *before* coding is free.
4. **Code, narrating.** Name variables properly (`left`/`right`, not `i`/`j` for
   two pointers). No dead code. Edge cases handled as you write, not after.
5. **Dry-run.** Take your example and step through your own code line by line, out
   loud, then state the final complexity and one improvement you'd make if asked.

**C++ specifics that cost people marks:** pass large objects by `const&`; know that
`unordered_map` is O(1) average but worst-case O(n) and that `map` is O(log n) and
ordered; `priority_queue<int>` is a **max**-heap, so for a min-heap write
`priority_queue<int, vector<int>, greater<int>>`; a custom comparator as a struct
with `bool operator()(const T& a, const T& b) const`; beware `int` overflow in
`mid = (lo+hi)/2` (use `lo + (hi-lo)/2`) and in multiplication-heavy DP; use
`long long` when a sum can exceed 2·10⁹.

**Mocks.** Two ways, both free:
- **Peer mock:** hand a classmate a problem they know and you don't. Their job is
  to interrupt every 5 minutes with "why that data structure?" and "what's the
  space complexity?". Do at least two.
- **Mock with me, in this chat, any day.** Say *"run a mock"* and I'll play the
  interviewer: pick a problem at your level, make you clarify and narrate, push
  back on your approach, ask for complexity, add a follow-up constraint after your
  first solution (the thing real interviewers always do), and score your answer
  against what a product-company bar-raiser would say. This is the highest-value
  thing I can do for you and it costs you nothing.

---

## 8. claimsight as interview material (don't waste it)

You have a project with real numbers, real bugs and a refuted hypothesis. Rounds
2 and 3 at most product companies will spend 10–15 minutes on it. Prepare:

- **60-second version** and **2-minute version** — both are written out in
  `prep/project-qa.md` §1 and §2. Memorise the 60-second one.
- **Three STAR stories** for behavioural/LP rounds, all true and all from this
  project: (1) *dive deep / insist on highest standards* — the 37-flag
  nondeterminism bug you found by testing a claim you'd written in a manifest;
  (2) *are right a lot / earn trust* — the case-mix hypothesis you tested, lost,
  and reported honestly instead of quietly reverting; (3) *deliver results / bias
  for action* — the DC small-MAD artifact and the guards you built after it.
- **Numbers you can say without looking:** 197,283 rows · 4,207 flagged (2.13%) ·
  43% of DRG×HRR groups are singletons · ~8 s pipeline on 2 cores · 287 tests.

**Rule I'll hold you to, and you should hold me to:** the project is *finished*. No
new features during these 28 days. If an interviewer's question reveals a gap in
your understanding of the project, we fix the *explanation*, not the code.

---

## 9. If you're ahead, or behind

**Behind at day 14** (checkpoint failed): cut every problem marked *stretch*, cut
Week 4's DP IV and bits, and use the freed time for redo-list work plus the
**minimum viable set** below. A candidate who reliably solves mediums beats one who
sometimes solves hards.

**Minimum viable set (~55, do these if everything else burns):** 1, 49, 128, 347,
560, 238, 15, 11, 3, 76, 424, 20, 739, 42, 704, 34, 33, 153, 875, 206, 19, 143,
146, 104, 543, 572, 102, 199, 98, 235, 215, 973, 295, 200, 994, 207, 210, 547,
743, 78, 46, 39, 22, 79, 56, 435, 55, 45, 134, 70, 198, 322, 300, 1143, 5.

**Ahead at day 21** (mediums consistently under 25 min): add hards in your three
strongest patterns only — 23, 76, 84, 124, 239, 41, 4, 72, 32, 312 — and start
doing LeetCode contest Q3/Q4 as your daily "new problem". Also add system-design
flavours relevant to your project (how would you serve this at 100× the data?),
since that's a natural extension of claimsight and interviewers like a candidate
who can extend their own work.

---

## 10. Starting tomorrow

1. Open a spreadsheet with the six tracking columns from §4.
2. Do **Day 1** exactly as written — the diagnostic first, no hints, timed. Do not
   skip it: everything after depends on knowing whether your 25-minute rule should
   be 20 or 35.
3. Tell me your diagnostic result (which of the 6 you solved, and the times) and
   I'll adjust Week 1 to match.
4. Then, whenever you want, say **"run a mock"** and we'll do one properly.

The other prep doc — SQL / DBMS / OS / OOP / CN revision checklist, weighted to
what product companies ask and cross-referenced to things you can answer *from
claimsight* — comes next. It's designed to fit in the 30 minutes at the end of the
daily block from Week 2 onward, so it doesn't compete with this.
