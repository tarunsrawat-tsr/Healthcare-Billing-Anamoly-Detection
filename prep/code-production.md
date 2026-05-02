# From "I know the approach" to working C++ code

You said: *"I somewhat knew the solution approach, but couldn't write it properly."*
Then, asked to pin it down:

| Question | Your answer |
|---|---|
| Where does it break? | **"C++ syntax fights me — I know exactly what to write"** |
| How precise was the approach? | **"I knew the techniques, and somewhat of approach but not complete solution"** |
| Which family is worst? | **"All of them equally"** |
| What do you do before typing? | **"Rough sketch first"** (a line or two of comments) |

## 1. Diagnosis

**"All of them equally" is the most informative answer there.** A DSA-knowledge gap
is never uniform — it clusters in DP, or graph traversal, or binary-search
boundaries, because those are the places the ideas are hard. Uniform failure across
every family means the difficulty isn't in the ideas. It's in the hands.

So you have two separate problems, and they need different fixes:

**Problem A — the approach is a shape, not a spec.** "Somewhat of approach but not
complete solution" means you can name the technique and sketch it, but the details
that decide correctness (what exactly the variable holds, where the answer gets
updated, which endpoint you're allowed to discard) are still unresolved when you
start typing. You then discover them *while* typing, which is why the structure
collapses. Fix: §3, the six-line spec.

**Problem B — C++ production fluency.** You know what to write and lose time and
correctness expressing it. Fix: §4, typing reps against `cpp-templates.cpp`.

**This is good news, and I want to be clear about why.** Problem B is a *finite*
list. There are roughly 25 code shapes that cover 90% of what you'll be asked to
write, and about 15 language traps that cause most wrong answers. That's two weeks
of deliberate practice, not two months of grinding problems. Most people never
diagnose this and spend a month doing more problems while the same syntax errors
keep costing them 15 minutes each.

**The 10-second self-test to tell A from B on any given problem:** try to write the
six lines in §3. If you can produce them in under three minutes, your spec is fine
and you're hitting Problem B — go type it. If you can't, you have Problem A on this
problem, and no amount of syntax practice will help; go back to the pattern and
derive the invariant properly.

---

## 2. What "properly" means

Code you can hand to an interviewer has four properties. When you say "couldn't
write it properly", one of these is what you mean, and it's worth knowing which:

1. **Complete** — every line exists, including the ones that feel trivial (the
   `{0: 1}` seed, the `return`, the `visited` mark). Most "I knew it but couldn't
   write it" cases are actually *incomplete* code, not wrong code.
2. **Correct at the boundaries** — `<` vs `<=`, `n-1` vs `n`, which side you
   discard. Decided by your invariant, not by testing and hoping.
3. **Expressible** — you can say it in C++ without looking anything up.
4. **Dry-runnable** — you can walk an input through it line by line and predict the
   output. If you can't, you don't yet have the code, you have a vibe.

Property 4 is the gate. **Never submit code you haven't dry-run.** It feels slow
and it saves enormous time, because a bug found by dry-run costs 20 seconds and a
bug found by the judge costs a re-read, a re-think, and usually a rewrite.

---

## 3. The six-line spec (fixes Problem A)

You currently write "a line or two of comments". Upgrade that to six fixed lines,
written **before any code**, on paper or in comments. Two to four minutes. This is
the difference between a shape and a spec.

```
1. SIZE      n <= ? therefore which complexity is acceptable
2. STATE     every variable, and EXACTLY what it holds at any moment
3. INVARIANT what is always true at the top of the loop
4. STEP      what one iteration does, in order
5. ANSWER    where the answer is updated, and why there rather than elsewhere
6. EDGE      empty / single element / all identical / negatives / overflow
```

**Worked example — Longest Substring Without Repeating Characters:**

```
1. SIZE      |s| <= 5e4 -> O(n) or O(n log n); O(n^2) will time out
2. STATE     left  = first index of the current window
             right = last index of the current window  (both inclusive)
             last[c] = most recent index at which character c appeared
3. INVARIANT at the top of each iteration, s[left..right-1] has all distinct chars
4. STEP      move right one step. If s[right] occurred at index p AND p >= left,
             set left = p + 1. Then record last[s[right]] = right.
5. ANSWER    AFTER the jump, because only then is the window valid again.
             (In the shortest-valid-window variant it goes INSIDE the shrink
             loop instead -- see cpp-templates.cpp section 5.)
6. EDGE      "" -> 0. "aaaa" -> 1. The stale-index case: if p < left you must NOT
             move left backwards, or the window grows and the answer is too large.
```

That is now codeable in 90 seconds with essentially no chance of a boundary bug,
because line 6 already caught the trap that breaks most attempts.

**Line 5 is the one that separates people who "know" sliding window from people who
can write it.** Longest-valid updates *after* shrinking; shortest-valid updates
*inside* the shrink loop. Backwards compiles, runs, and is wrong.

**Line 3 is the one that fixes binary search forever.** If you can state the
invariant, the boundaries follow mechanically. If you can't, you'll be guessing at
`<` vs `<=` every time — which is exactly the "all families equally" feeling.

Do this for every problem in the 28-day plan for the first week. By week three
you'll be doing lines 2, 3 and 5 in your head, which is the actual goal.

---

## 4. The typing drill (fixes Problem B)

`prep/cpp-templates.cpp` is ~970 lines of the shapes you need, and — unlike every
blog-post template you've ever copied — **it compiles clean under `-Wall -Wextra`
and passes its own asserts under C++17, C++20, ASan and UBSan.** Every template is
a real function with a test in `main()`. You can trust it.

```
g++ -std=c++17 -O2 -Wall -Wextra -o /tmp/t prep/cpp-templates.cpp && /tmp/t
```

**The drill (15–20 minutes):**

1. Pick the day's shapes from the table below.
2. **Close the file.** Open a blank file.
3. Type the shape from memory — the function signature, the body, and one assert
   that proves it works.
4. Compile. **Count the compiles it takes to go green.**
5. Diff against the real thing. Every difference is data: was it a syntax slip
   (fix: more reps) or a logic slip (fix: your spec was fuzzy — back to §3)?

**The metric is COMPILES-TO-GREEN.** Not problems solved, not minutes. Track
`shape | compiles | minutes | what broke`. Target: **1 compile, under 6 minutes,
for any shape, by day 10.** When a shape needs more than one compile it goes back
on tomorrow's list. That single number will tell you more about your readiness than
anything else you're currently measuring.

| Day | Shapes (section numbers in the .cpp) |
|---|---|
| 1 | §1 declarations · §7 binary search (both forms) · §4 two pointers |
| 2 | §5 sliding window (longest **and** shortest) · §6 prefix sum · §8 monotonic stack |
| 3 | §9 grid BFS · grid DFS · §10 tree DFS + level-order BFS |
| 4 | §9 Kahn topo sort · union-find · Dijkstra · §2 comparators |
| 5 | §3 heaps (all three comparator forms) · §11 backtracking (all three) |
| 6 | §12 DP (tabulation, 2-D, 1-D knapsack, memo with lambda) · §13 trie |
| 7 | §14 LRU cache · §15 gotchas — then redo your three worst shapes |
| 8–10 | Three random shapes a day until compiles-to-green is 1 for all of them |

**After day 10**, the drill drops to one shape a day as a warm-up, and it stays
there. This is not a month-long tax; it's a two-week fix for a specific deficit.

---

## 5. What's in the template file

| § | Shape | The line people get wrong |
|---|---|---|
| 1 | Declarations | `vector<vector<int>> g(n, vector<int>(m, -1))` — the inner vector |
| 2 | Sorting & comparators | `<=` in a comparator is undefined behaviour. Never. |
| 3 | Heaps | `comp(a,b)` true ⇒ a sorts before b, and **top() sorts LAST**. So `>` gives a min-heap. |
| 4 | Two pointers | You must be able to say *which* endpoint you discarded and *why* |
| 5 | Sliding window | Where the answer is updated (§3, line 5) |
| 6 | Prefix sum + map | The `{0: 1}` seed. Without it every subarray starting at index 0 is missed. |
| 7 | Binary search | Learn ONE form: half-open `[lo,hi)`, `while (lo < hi)`, `hi = mid` on true. It does everything. |
| 8 | Monotonic stack | Store **indices**, not values |
| 9 | Grid / topo / DSU / Dijkstra | Mark visited **at push time**, not at pop |
| 10 | Trees | Update the global answer inside the recursion, but **return only one arm** |
| 11 | Backtracking | The **un-choose**. Forgetting it corrupts every later branch. |
| 12 | DP | Recurrence as a function of indices first; table second. Never start with the table. |
| 13 | Trie | `int i = ch - 'a'` — 20 lines total |
| 14 | LRU cache | `list::splice` moves a node to the front in O(1) **without invalidating the iterator**. That's the whole trick. |
| 15 | Gotchas | See below |

**The gotchas, all of which produce code that compiles and is wrong:** unsigned
`s.size() - 1` on an empty container · `m[key]` default-inserts (use `count`/`find`)
· `priority_queue` is a **max**-heap by default · overflow in `(lo+hi)/2` and in
products · `accumulate(..., 0)` is an `int` accumulator (`0LL` for long long) ·
`unordered_map<pair<int,int>, V>` needs a custom hash · erasing while iterating
needs `it = v.erase(it)` · `lower_bound` may return `end()` — check before
dereferencing · `substr(start, LENGTH)` not end index · integer division truncates
toward zero (ceil needs `(a+b-1)/b`) · `vector<bool>` is bit-packed · `assert()` is
a **macro**, so a top-level comma (from `{1, 2}`) needs an extra pair of parens ·
`pair<int,string>` never compares equal to `make_pair(1, "b")` — the literal deduces
to `const char*`.

The last two are in the file because I hit them while writing it, which is a fair
sample of how this goes for everyone.

**Keep your own list.** When the same compiler error hits you twice in one day,
write it in a comment at the top of your practice file. By day 28 you'll have your
personal ten, and they'll stop costing you time.

---

## 6. How this fits the 28-day plan without adding hours

You have 3–4 h/day and I'm not going to ask for more. The drill **replaces** part
of the existing block for ten days:

| Block slot | Days 1–10 | Days 11–28 |
|---|---|---|
| 0:00–0:20 redo | unchanged | unchanged |
| 0:20–0:45 pattern study | **becomes the typing drill** | back to pattern study, plus 1 shape as warm-up |
| 0:45–2:45 problems | unchanged, but write the §3 spec first | unchanged |
| 2:45–3:10 write-up | add "syntax slip or logic slip?" per failure | unchanged |
| 3:10–3:30 recall | unchanged | unchanged |

Typing a template from memory *is* pattern study — you're internalising the
invariant and the syntax at the same time — so nothing is lost by the swap.

And the §3 spec costs about three minutes per problem, taken from your existing
25-minute attempt, not added to it. It pays for itself immediately: the problems
you currently fail at minute 25 with half-written code, you'll fail at minute 8
with a precise spec, which means you'll get to read the approach and re-solve it
the same day.

---

## 7. What I can do with you, live

- **"Compile this"** — paste code you wrote and I'll build it with
  `g++ -std=c++17 -Wall -Wextra` in the sandbox, plus ASan/UBSan if you want, and
  give you the compiler's actual output rather than my opinion of it. Useful when
  an error message is incomprehensible, which is often.
- **"Run a drill"** — I name a shape, you type it here, I diff it against the
  reference and score compiles-to-green. Same as the drill, with an examiner.
- **"Spec check"** — paste your six lines for a problem *before* you code it and
  I'll tell you whether they're precise enough to code from, and which line is
  missing. This is the cheapest way to fix Problem A, because the gap is invisible
  from inside your own head.
- **"Run a mock"** — full 45-minute interview: I pick a problem at your level, you
  clarify and narrate, I push back on your approach, ask for complexity mid-code,
  add a follow-up constraint after your first solution, and score it against what a
  product-company bar-raiser would actually say.

Start with the §3 spec on one problem you recently failed. Paste the six lines and
I'll tell you which one was missing — that alone will probably explain the failure.
