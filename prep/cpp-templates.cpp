// prep/cpp-templates.cpp -- the ~25 code shapes that appear in ~90% of SDE rounds.
//
// This file is deliberately COMPILABLE and SELF-TESTING. Every template below is
// a real function with an assert in main(). That matters: a template in a blog
// post can be subtly wrong and you will spend 20 minutes debugging the reference
// instead of learning it. This one is verified.
//
//     g++ -std=c++17 -O2 -Wall -Wextra -o /tmp/t prep/cpp-templates.cpp && /tmp/t
//
// HOW TO USE IT (see prep/code-production.md for the full drill):
//   1. Read a shape, understand the invariant in its header comment.
//   2. Close the file. Type it from memory into a blank file.
//   3. Compile and run. Count how many compiles it took to go green.
//   4. Any shape needing more than one compile is back on tomorrow's list.
//
// Metric to track: COMPILES-TO-GREEN. Target is 1 by day 10. That number, not
// "problems solved", is what fixes the gap you described.

#include <algorithm>
#include <array>
#include <cassert>
#include <cctype>
#include <climits>
#include <functional>
#include <iostream>
#include <list>
#include <map>
#include <numeric>
#include <queue>
#include <set>
#include <stack>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace std;

// ============================================================================
// 1. DECLARATIONS YOU MUST BE ABLE TO TYPE WITHOUT THINKING
//    Every one of these is a place people lose 2 minutes and introduce a bug.
// ============================================================================

void declarations() {
    int n = 5, m = 3;

    vector<int> a(n, 0);                              // n zeros
    vector<vector<int>> grid(n, vector<int>(m, -1));   // n x m of -1  <-- note the inner vector
    vector<vector<pair<int, int>>> adj(n);             // adjacency list, weighted
    vector<int> indeg(n, 0);
    vector<bool> used(n, false);                       // vector<bool> is bit-packed; fine for flags

    unordered_map<int, int> freq;                      // O(1) average, unordered
    unordered_set<string> seen;
    map<int, int> ordered;                             // O(log n), SORTED, has lower_bound
    set<pair<int, int>> pts;

    stack<int> st;
    queue<int> q;
    deque<int> dq;                                     // sliding-window maximum

    priority_queue<int> maxHeap;                       // MAX-heap by default -- the classic surprise
    priority_queue<int, vector<int>, greater<int>> minHeap;   // MIN-heap: the 3-arg form
    priority_queue<pair<int, int>, vector<pair<int, int>>, greater<>> pqPair;  // min on .first

    string s = "abc";
    pair<int, int> p = {1, 2};                         // brace init, no make_pair needed
    array<int, 4> dr = {-1, 0, 1, 0};                  // grid directions
    array<int, 4> dc = {0, 1, 0, -1};

    assert(a.size() == 5 && grid[0][0] == -1 && dr[0] == -1 && dc[3] == -1);
    assert(dr.size() == 4 && dc.size() == 4);                  // the four 8-neighbour offsets pair up
    assert(maxHeap.empty() && minHeap.empty() && pqPair.empty());
    assert(s.size() == 3 && p.second == 2 && !used[0] && indeg[0] == 0);
    (void)freq; (void)seen; (void)ordered; (void)pts; (void)st; (void)q; (void)dq; (void)adj;
}

// ============================================================================
// 2. SORTING AND COMPARATORS
//    The bug: a comparator that is not a STRICT WEAK ORDERING. `<=` in a sort
//    comparator is undefined behaviour and will crash or corrupt on real inputs.
//    Rule: the comparator answers "does a go strictly before b?" -- never use <=.
// ============================================================================

vector<vector<int>> mergeIntervals(vector<vector<int>> iv) {
    // Sort by start, tie-break by end. Deterministic total order.
    sort(iv.begin(), iv.end(), [](const vector<int>& x, const vector<int>& y) {
        return x[0] != y[0] ? x[0] < y[0] : x[1] < y[1];
    });
    vector<vector<int>> out;
    for (const auto& cur : iv) {
        if (!out.empty() && cur[0] <= out.back()[1]) {
            out.back()[1] = max(out.back()[1], cur[1]);   // extend, don't append
        } else {
            out.push_back(cur);
        }
    }
    return out;
}

void sortingExtras() {
    vector<int> v = {3, 1, 2};
    sort(v.rbegin(), v.rend());                          // descending, no comparator
    assert((v == vector<int>{3, 2, 1}));

    // Sort a vector of pairs: sorts by .first, then .second. No comparator needed.
    vector<pair<int, int>> vp = {{2, 9}, {1, 5}, {2, 3}};
    sort(vp.begin(), vp.end());
    assert(vp[0] == make_pair(1, 5) && vp[2] == make_pair(2, 9));

    // Sort by the SECOND element: you do need a comparator.
    sort(vp.begin(), vp.end(), [](const auto& x, const auto& y) { return x.second < y.second; });
    assert(vp[0].second == 3);

    // Sort indices by value, without moving the data.
    vector<int> w = {30, 10, 20};
    vector<int> idx(w.size());
    iota(idx.begin(), idx.end(), 0);                     // 0,1,2
    sort(idx.begin(), idx.end(), [&](int i, int j) { return w[i] < w[j]; });
    assert((idx == vector<int>{1, 2, 0}));

    assert(*max_element(w.begin(), w.end()) == 30);
    assert(accumulate(w.begin(), w.end(), 0) == 60);
    assert(accumulate(w.begin(), w.end(), 0LL) == 60);   // 0LL forces long long -- overflow guard
}

// ============================================================================
// 3. HEAPS -- where the custom comparator lives, and where the inversion bites
//
//    priority_queue's comparator reads BACKWARDS compared to sort's, and reasoning
//    about it under pressure is how you lose ten minutes. So don't reason about it
//    -- use the one rule that is always true:
//
//      comp(a, b) returns true when a sorts BEFORE b.
//      top() is the element that sorts LAST.
//
//    Consequences, all of which fall out of that one sentence:
//      - want the SMALLEST on top -> comparator uses '>' (greater<>)  = MIN-heap
//      - want the LARGEST  on top -> comparator uses '<' (less<>, the default) = MAX-heap
//      - want the WORST on top so you can pop it (top-K) -> comparator returns
//        true when a is BETTER than b
//    Yes: '>' gives you a min-heap. Memorise it and stop re-deriving it.
//
//    For 90% of cases you never need a custom comparator at all: pack what you
//    want to order by into a pair's .first and use greater<>.
// ============================================================================

struct ByDist {                                          // struct form -- preferred in interviews
    bool operator()(const pair<int, int>& x, const pair<int, int>& y) const {
        return x.first > y.first;                        // '>' sorts descending -> smallest on top
    }
};

void heaps() {
    // Kth largest: keep a MIN-heap of size k. Top is the answer.
    auto kthLargest = [](vector<int> v, int k) {
        priority_queue<int, vector<int>, greater<int>> h;
        for (int x : v) {
            h.push(x);
            if ((int)h.size() > k) h.pop();              // cast: size() is unsigned
        }
        return h.top();
    };
    assert(kthLargest({3, 2, 1, 5, 6, 4}, 2) == 5);

    // Struct comparator.
    priority_queue<pair<int, int>, vector<pair<int, int>>, ByDist> pq;
    pq.push({5, 0}); pq.push({1, 1}); pq.push({3, 2});
    assert(pq.top().first == 1);                         // smallest dist first

    // Lambda comparator -- needs decltype AND the constructor argument.
    // Forgetting `(cmp)` at the end is the most common error here.
    auto cmp = [](const pair<int, string>& x, const pair<int, string>& y) {
        if (x.first != y.first) return x.first > y.first;   // min by count
        return x.second < y.second;                          // then max by lexicographic
    };
    priority_queue<pair<int, string>, vector<pair<int, string>>, decltype(cmp)> pq2(cmp);
    pq2.push({1, "b"}); pq2.push({1, "a"}); pq2.push({2, "c"});
    assert(pq2.top() == make_pair(1, string("b")));            // gotcha (n): literal != string

    // Top K Frequent Words: keep a heap of size k whose TOP is the WORST candidate
    // so it can be popped. Per the rule, comp(a,b) must return true when a is
    // BETTER: higher frequency first, then lexicographically smaller word first.
    // That leaves the worst -- lowest frequency, largest word -- on top.
    vector<string> words = {"i", "love", "leetcode", "i", "love", "this"};
    unordered_map<string, int> cnt;
    for (const auto& w : words) ++cnt[w];
    auto better = [](const pair<string, int>& a, const pair<string, int>& b) {
        if (a.second != b.second) return a.second > b.second;   // higher count is better
        return a.first < b.first;                               // smaller word is better
    };
    priority_queue<pair<string, int>, vector<pair<string, int>>, decltype(better)> h(better);
    const int k = 2;
    for (const auto& kv : cnt) {
        h.push(kv);
        if ((int)h.size() > k) h.pop();                         // evict the worst
    }
    vector<string> top;
    while (!h.empty()) { top.push_back(h.top().first); h.pop(); }
    reverse(top.begin(), top.end());                            // pops come out worst-first
    assert((top == vector<string>{"i", "love"}));
}

// ============================================================================
// 4. TWO POINTERS (opposite ends)
//    Invariant: the answer, if it exists, is always within [lo, hi]. Each step
//    must provably discard one endpoint -- if you can't say which and why, the
//    two-pointer move is not justified and you need a different approach.
// ============================================================================

vector<vector<int>> threeSum(vector<int> a) {
    sort(a.begin(), a.end());
    int n = (int)a.size();
    vector<vector<int>> out;
    for (int i = 0; i + 2 < n; ++i) {
        if (i > 0 && a[i] == a[i - 1]) continue;         // skip duplicate first element
        int lo = i + 1, hi = n - 1;
        while (lo < hi) {
            int sum = a[i] + a[lo] + a[hi];
            if (sum < 0) {
                ++lo;
            } else if (sum > 0) {
                --hi;
            } else {
                out.push_back({a[i], a[lo], a[hi]});
                while (lo < hi && a[lo] == a[lo + 1]) ++lo;   // skip duplicate pairs
                while (lo < hi && a[hi] == a[hi - 1]) --hi;
                ++lo; --hi;                                    // BOTH move, or you loop forever
            }
        }
    }
    return out;
}

int maxArea(vector<int>& h) {                            // Container With Most Water
    int lo = 0, hi = (int)h.size() - 1, best = 0;
    while (lo < hi) {
        best = max(best, min(h[lo], h[hi]) * (hi - lo));
        if (h[lo] < h[hi]) ++lo; else --hi;              // discard the SHORTER side: it can never win
    }
    return best;
}

// ============================================================================
// 5. SLIDING WINDOW -- the two variants, and the one line that differs
//
//    LONGEST valid window:  update the answer AFTER the shrink loop.
//    SHORTEST valid window: update the answer INSIDE the shrink loop.
//    Get this backwards and the code compiles, runs, and is wrong.
//
//    Window convention used here: [left, right], both inclusive.
// ============================================================================

int longestUniqueSubstring(const string& s) {            // LONGEST variant
    unordered_map<char, int> last;
    int left = 0, best = 0;
    for (int right = 0; right < (int)s.size(); ++right) {
        char c = s[right];
        auto f = last.find(c);
        if (f != last.end() && f->second >= left) {      // >= left, or a stale index jumps you back
            left = f->second + 1;
        }
        last[c] = right;
        best = max(best, right - left + 1);              // AFTER shrinking
    }
    return best;
}

int longestAtMostKDistinct(const string& s, int k) {     // LONGEST variant, generic count form
    unordered_map<char, int> cnt;
    int left = 0, best = 0;
    for (int right = 0; right < (int)s.size(); ++right) {
        ++cnt[s[right]];
        while ((int)cnt.size() > k) {                    // while INVALID
            if (--cnt[s[left]] == 0) cnt.erase(s[left]); // erase at zero, or size() lies
            ++left;
        }
        best = max(best, right - left + 1);              // valid here -> update
    }
    return best;
}

string minWindow(const string& s, const string& t) {     // SHORTEST variant
    unordered_map<char, int> need;
    for (char c : t) ++need[c];
    int missing = (int)t.size(), left = 0, bestLen = INT_MAX, bestStart = 0;
    for (int right = 0; right < (int)s.size(); ++right) {
        if (need[s[right]]-- > 0) --missing;             // only counts chars still owed
        while (missing == 0) {                           // while VALID
            if (right - left + 1 < bestLen) {            // INSIDE the shrink loop
                bestLen = right - left + 1;
                bestStart = left;
            }
            if (++need[s[left]] > 0) ++missing;          // undo left's contribution
            ++left;
        }
    }
    return bestLen == INT_MAX ? "" : s.substr(bestStart, bestLen);
}

// ============================================================================
// 6. PREFIX SUM + HASHMAP
//    Invariant: count[p] = number of prefixes seen SO FAR (strictly before the
//    current index) whose sum equals p. Seed with {0: 1} -- the empty prefix --
//    or every subarray starting at index 0 is missed. That seed is THE bug.
// ============================================================================

long long subarraySumK(const vector<int>& a, int k) {
    unordered_map<long long, int> count{{0, 1}};         // <-- the seed
    long long prefix = 0, out = 0;
    for (int x : a) {
        prefix += x;
        auto f = count.find(prefix - k);                 // find, not [] -- [] would insert
        if (f != count.end()) out += f->second;
        ++count[prefix];                                 // insert AFTER querying
    }
    return out;
}

// Product of Array Except Self: prefix product from the left, suffix from the
// right, no division (division breaks on zeros).
vector<int> productExceptSelf(const vector<int>& a) {
    int n = (int)a.size();
    vector<int> out(n, 1);
    for (int i = 1; i < n; ++i) out[i] = out[i - 1] * a[i - 1];   // out[i] = product of a[0..i-1]
    int suffix = 1;
    for (int i = n - 1; i >= 0; --i) {
        out[i] *= suffix;
        suffix *= a[i];
    }
    return out;
}

// ============================================================================
// 7. BINARY SEARCH -- learn ONE form and derive everything from it
//
//    Form B below ("first index where the predicate becomes true") handles
//    exact search, insert position, first/last occurrence, and
//    binary-search-on-the-answer. Half-open [lo, hi), loop while lo < hi,
//    hi = mid on true, lo = mid + 1 on false. No off-by-one is possible.
// ============================================================================

int bsExact(const vector<int>& a, int target) {          // Form A: closed interval, exact match
    int lo = 0, hi = (int)a.size() - 1;                  // hi = n-1
    while (lo <= hi) {                                   // <= pairs with hi = n-1. Never mix forms.
        int mid = lo + (hi - lo) / 2;                    // overflow-safe
        if (a[mid] == target) return mid;
        if (a[mid] < target) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
}

// Form B: first index in [0, n) where ok(i) is true. ok must be monotone
// (false...false true...true). Returns n if never true.
int bsFirstTrue(int n, const function<bool(int)>& ok) {
    int lo = 0, hi = n;                                  // hi = n, half-open
    while (lo < hi) {                                    // < pairs with hi = n
        int mid = lo + (hi - lo) / 2;
        if (ok(mid)) hi = mid; else lo = mid + 1;
    }
    return lo;
}

int lowerIdx(const vector<int>& a, int t) {              // first index with a[i] >= t
    return bsFirstTrue((int)a.size(), [&](int i) { return a[i] >= t; });
}
int upperIdx(const vector<int>& a, int t) {              // first index with a[i] > t
    return bsFirstTrue((int)a.size(), [&](int i) { return a[i] > t; });
}

pair<int, int> firstAndLast(const vector<int>& a, int t) {
    int first = lowerIdx(a, t);
    if (first == (int)a.size() || a[first] != t) return {-1, -1};
    return {first, upperIdx(a, t) - 1};                  // last = (first index > t) - 1
}

int searchRotated(const vector<int>& a, int t) {         // one half is always sorted
    int lo = 0, hi = (int)a.size() - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (a[mid] == t) return mid;
        if (a[lo] <= a[mid]) {                           // left half sorted
            if (a[lo] <= t && t < a[mid]) hi = mid - 1; else lo = mid + 1;
        } else {                                         // right half sorted
            if (a[mid] < t && t <= a[hi]) lo = mid + 1; else hi = mid - 1;
        }
    }
    return -1;
}

// Binary search ON THE ANSWER: minimise the maximum. Guess a value, check
// feasibility greedily. The check function is where the real work is.
int kokoBananas(const vector<int>& piles, int h) {
    auto canEatAt = [&](int speed) {
        long long hours = 0;
        for (int p : piles) hours += (p + speed - 1) / speed;   // ceil without floating point
        return hours <= h;
    };
    int lo = 1, hi = *max_element(piles.begin(), piles.end());
    while (lo < hi) {
        int mid = lo + (hi - lo) / 2;
        if (canEatAt(mid)) hi = mid; else lo = mid + 1;
    }
    return lo;
}

// ============================================================================
// 8. MONOTONIC STACK
//    "Next greater / next smaller / largest rectangle" => one pass, O(n).
//    Invariant: the stack holds INDICES whose values are strictly decreasing
//    (bottom to top). Store indices, not values -- you need positions.
// ============================================================================

vector<int> nextGreater(const vector<int>& a) {
    int n = (int)a.size();
    vector<int> out(n, -1);
    stack<int> st;                                       // indices, decreasing values
    for (int i = 0; i < n; ++i) {
        while (!st.empty() && a[i] > a[st.top()]) {
            out[st.top()] = a[i];
            st.pop();
        }
        st.push(i);
    }
    return out;
}

vector<int> dailyTemperatures(const vector<int>& t) {    // same shape, answer is the distance
    int n = (int)t.size();
    vector<int> out(n, 0);
    stack<int> st;
    for (int i = 0; i < n; ++i) {
        while (!st.empty() && t[i] > t[st.top()]) {
            out[st.top()] = i - st.top();                // index arithmetic, not values
            st.pop();
        }
        st.push(i);
    }
    return out;
}

// ============================================================================
// 9. GRAPHS: BFS/DFS on a grid, topological sort, union-find, Dijkstra
// ============================================================================

// Grid BFS. The four lines people fumble: bounds check, visited-at-push (not at
// pop), the directions arrays, and pushing {r,c} as a pair.
int shortestPathBinaryMatrix(vector<vector<int>> g) {
    int n = (int)g.size(), m = (int)g[0].size();
    if (g[0][0] == 1 || g[n - 1][m - 1] == 1) return -1;
    const int dr[8] = {-1, -1, -1, 0, 0, 1, 1, 1};
    const int dc[8] = {-1, 0, 1, -1, 1, -1, 0, 1};
    vector<vector<bool>> vis(n, vector<bool>(m, false));
    queue<array<int, 2>> q;
    q.push({0, 0});
    vis[0][0] = true;                                    // mark AT PUSH TIME
    int steps = 1;
    while (!q.empty()) {
        int sz = (int)q.size();                          // level snapshot, like tree BFS
        while (sz--) {
            auto [r, c] = q.front(); q.pop();
            if (r == n - 1 && c == m - 1) return steps;
            for (int k = 0; k < 8; ++k) {
                int nr = r + dr[k], nc = c + dc[k];
                if (nr < 0 || nr >= n || nc < 0 || nc >= m) continue;
                if (g[nr][nc] == 1 || vis[nr][nc]) continue;
                vis[nr][nc] = true;
                q.push({nr, nc});
            }
        }
        ++steps;
    }
    return -1;
}

// Grid DFS -- count islands. Mutating the input as the visited marker is fine in
// an interview if you say you're doing it; otherwise take a copy.
void sinkIsland(vector<vector<char>>& g, int r, int c) {
    int n = (int)g.size(), m = (int)g[0].size();
    if (r < 0 || r >= n || c < 0 || c >= m || g[r][c] != '1') return;
    g[r][c] = '0';                                       // mark visited
    sinkIsland(g, r + 1, c); sinkIsland(g, r - 1, c);
    sinkIsland(g, r, c + 1); sinkIsland(g, r, c - 1);
}

int numIslands(vector<vector<char>> g) {
    int out = 0;
    for (int r = 0; r < (int)g.size(); ++r)
        for (int c = 0; c < (int)g[0].size(); ++c)
            if (g[r][c] == '1') { ++out; sinkIsland(g, r, c); }
    return out;
}

// Kahn's topological sort: in-degree array + queue of zero-in-degree nodes.
// Easier to write correctly than DFS-based topo sort, and the cycle check is free.
vector<int> topoOrder(int n, const vector<vector<int>>& adj) {
    vector<int> indeg(n, 0);
    for (int u = 0; u < n; ++u)
        for (int v : adj[u]) ++indeg[v];
    queue<int> q;
    for (int i = 0; i < n; ++i) if (indeg[i] == 0) q.push(i);
    vector<int> order;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        order.push_back(u);
        for (int v : adj[u]) if (--indeg[v] == 0) q.push(v);
    }
    if ((int)order.size() != n) return {};               // a cycle exists
    return order;
}

// Union-Find with path halving and union by size. ~12 lines, near-O(1) per op.
struct DSU {
    vector<int> parent, sz;
    explicit DSU(int n) : parent(n), sz(n, 1) { iota(parent.begin(), parent.end(), 0); }
    int find(int x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    }
    bool unite(int a, int b) {                           // false if already connected
        a = find(a); b = find(b);
        if (a == b) return false;
        if (sz[a] < sz[b]) swap(a, b);
        parent[b] = a; sz[a] += sz[b];
        return true;
    }
};

// Dijkstra. Note: no custom comparator needed -- pack (dist, node) into a pair
// and use greater<>. Skip stale entries with `if (d > dist[u]) continue;`.
vector<int> dijkstra(int n, const vector<vector<pair<int, int>>>& adj, int src) {
    const int INF = INT_MAX;
    vector<int> dist(n, INF);
    priority_queue<pair<int, int>, vector<pair<int, int>>, greater<>> pq;
    dist[src] = 0;
    pq.push({0, src});
    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();
        if (d > dist[u]) continue;                       // stale heap entry
        for (auto [v, w] : adj[u]) {
            if (d != INF && d + w < dist[v]) {           // d + w can overflow if d == INT_MAX
                dist[v] = d + w;
                pq.push({dist[v], v});
            }
        }
    }
    return dist;
}

// ============================================================================
// 10. TREES
//     The two shapes: (a) DFS that returns aggregated info upward, and
//     (b) BFS by levels with a size snapshot.
//     For global answers (diameter, max path sum): update the global INSIDE the
//     recursion but RETURN only the single best arm. Conflating the two is the
//     classic tree bug.
// ============================================================================

struct TreeNode {
    int val;
    TreeNode *left, *right;
    explicit TreeNode(int v) : val(v), left(nullptr), right(nullptr) {}
};

int maxDepth(TreeNode* n) {
    if (!n) return 0;
    return 1 + max(maxDepth(n->left), maxDepth(n->right));
}

int treeDiameter(TreeNode* root) {
    int best = 0;
    function<int(TreeNode*)> depth = [&](TreeNode* n) -> int {
        if (!n) return 0;
        int l = depth(n->left), r = depth(n->right);
        best = max(best, l + r);                         // path THROUGH this node: both arms
        return 1 + max(l, r);                            // but return only ONE arm upward
    };
    depth(root);
    return best;
}

bool isBst(TreeNode* n, long long lo, long long hi) {    // long long: node values can be INT_MIN/MAX
    if (!n) return true;
    if (n->val <= lo || n->val >= hi) return false;      // strict, both ends
    return isBst(n->left, lo, n->val) && isBst(n->right, n->val, hi);
}

vector<vector<int>> levelOrder(TreeNode* root) {
    vector<vector<int>> out;
    if (!root) return out;
    queue<TreeNode*> q;
    q.push(root);
    while (!q.empty()) {
        int sz = (int)q.size();                          // SNAPSHOT before the inner loop
        vector<int> level;
        while (sz--) {
            TreeNode* n = q.front(); q.pop();
            level.push_back(n->val);
            if (n->left) q.push(n->left);
            if (n->right) q.push(n->right);
        }
        out.push_back(level);
    }
    return out;
}

// ============================================================================
// 11. BACKTRACKING -- choose, recurse, UN-choose.
//     The un-choose is the part people forget, and forgetting it corrupts every
//     subsequent branch. Pass the accumulator by reference and restore it.
// ============================================================================

void subsetsHelper(const vector<int>& nums, int i, vector<int>& cur, vector<vector<int>>& out) {
    if (i == (int)nums.size()) { out.push_back(cur); return; }   // decide at the leaf
    cur.push_back(nums[i]);                                      // choose
    subsetsHelper(nums, i + 1, cur, out);
    cur.pop_back();                                              // UN-choose
    subsetsHelper(nums, i + 1, cur, out);                        // exclude branch
}

void permuteHelper(vector<int>& nums, vector<bool>& used, vector<int>& cur,
                   vector<vector<int>>& out) {
    if ((int)cur.size() == (int)nums.size()) { out.push_back(cur); return; }
    for (int i = 0; i < (int)nums.size(); ++i) {
        if (used[i]) continue;
        used[i] = true;  cur.push_back(nums[i]);
        permuteHelper(nums, used, cur, out);
        used[i] = false; cur.pop_back();
    }
}

// Combination Sum II: sorted input, skip duplicates AT THE SAME DEPTH only.
// `i > start` (not `i > 0`) is the line that makes it correct.
void combHelper(const vector<int>& c, int start, int target, vector<int>& cur,
                vector<vector<int>>& out) {
    if (target == 0) { out.push_back(cur); return; }
    for (int i = start; i < (int)c.size(); ++i) {
        if (i > start && c[i] == c[i - 1]) continue;             // duplicate at this depth
        if (c[i] > target) break;                                // sorted -> prune the rest
        cur.push_back(c[i]);
        combHelper(c, i + 1, target - c[i], cur, out);           // i+1: each element used once
        cur.pop_back();
    }
}

// ============================================================================
// 12. DP -- memoisation and tabulation
//     Write the recurrence as a function of indices FIRST. Only then decide
//     whether to memoise or build a table. Never start with the table.
// ============================================================================

int coinChange(const vector<int>& coins, int amount) {           // tabulation, unbounded
    const int INF = INT_MAX;
    vector<int> dp(amount + 1, INF);
    dp[0] = 0;
    for (int a = 1; a <= amount; ++a)
        for (int c : coins)
            if (c <= a && dp[a - c] != INF) dp[a] = min(dp[a], dp[a - c] + 1);
    return dp[amount] == INF ? -1 : dp[amount];
}

int longestCommonSubsequence(const string& a, const string& b) {  // tabulation, 2-D
    int n = (int)a.size(), m = (int)b.size();
    vector<vector<int>> dp(n + 1, vector<int>(m + 1, 0));         // +1 for the empty-prefix base
    for (int i = n - 1; i >= 0; --i)
        for (int j = m - 1; j >= 0; --j)
            dp[i][j] = (a[i] == b[j]) ? 1 + dp[i + 1][j + 1]
                                      : max(dp[i + 1][j], dp[i][j + 1]);
    return dp[0][0];
}

int knapsack01(const vector<int>& w, const vector<int>& v, int cap) {
    // 1-D optimisation: capacity must iterate DESCENDING, or one item is used twice.
    vector<int> dp(cap + 1, 0);
    for (int i = 0; i < (int)w.size(); ++i)
        for (int c = cap; c >= w[i]; --c)
            dp[c] = max(dp[c], dp[c - w[i]] + v[i]);
    return dp[cap];
}

int memoisedWithLambda(int n) {                                   // memo + std::function
    vector<int> memo(n + 1, -1);                                  // -1 = not computed
    function<int(int)> go = [&](int i) -> int {
        if (i <= 1) return i;
        int& cached = memo[i];                                    // reference: write-through
        if (cached != -1) return cached;
        return cached = go(i - 1) + go(i - 2);
    };
    return go(n);
}

// ============================================================================
// 13. TRIE -- 20 lines that make every prefix problem easy
// ============================================================================

struct Trie {
    struct Node {
        array<Node*, 26> child{};
        bool isEnd = false;
        ~Node() { for (Node* c : child) delete c; }          // cascades from the root
    };
    Node* root = new Node();
    ~Trie() { delete root; }
    void insert(const string& w) {
        Node* cur = root;
        for (char ch : w) {
            int i = ch - 'a';                                     // char -> index
            if (!cur->child[i]) cur->child[i] = new Node();
            cur = cur->child[i];
        }
        cur->isEnd = true;
    }
    bool search(const string& w) const {
        const Node* cur = root;
        for (char ch : w) {
            int i = ch - 'a';
            if (!cur->child[i]) return false;
            cur = cur->child[i];
        }
        return cur->isEnd;
    }
    bool startsWith(const string& p) const {
        const Node* cur = root;
        for (char ch : p) {
            int i = ch - 'a';
            if (!cur->child[i]) return false;
            cur = cur->child[i];
        }
        return true;
    }
};

// ============================================================================
// 14. DESIGN: LRU Cache -- the single most asked design question
//     list<pair<k,v>> for recency (front = newest) + unordered_map from key to
//     list ITERATOR. splice() moves a node to the front in O(1) without
//     invalidating the iterator -- that is the whole trick.
// ============================================================================

class LRUCache {
    int cap;
    list<pair<int, int>> items;                                   // front = most recent
    unordered_map<int, list<pair<int, int>>::iterator> pos;
public:
    explicit LRUCache(int capacity) : cap(capacity) {}
    int get(int key) {
        auto f = pos.find(key);
        if (f == pos.end()) return -1;
        items.splice(items.begin(), items, f->second);            // O(1) move to front
        return f->second->second;
    }
    void put(int key, int value) {
        auto f = pos.find(key);
        if (f != pos.end()) {
            f->second->second = value;
            items.splice(items.begin(), items, f->second);
            return;
        }
        if ((int)items.size() == cap) {                           // evict least recent
            pos.erase(items.back().first);
            items.pop_back();
        }
        items.emplace_front(key, value);
        pos[key] = items.begin();
    }
};

// ============================================================================
// 15. THE GOTCHAS -- each of these produces compiling, wrong code
// ============================================================================

struct PairHash {                                                 // unordered_map with pair keys
    size_t operator()(const pair<int, int>& p) const {
        return hash<long long>()(((long long)p.first << 32) ^ (unsigned int)p.second);
    }
};

void gotchas() {
    // (a) size() is UNSIGNED. size()-1 on an empty container is a huge number.
    string s = "";
    assert(s.size() == 0);
    //   for (int i = s.size() - 1; i >= 0; --i)   <-- never terminates
    int n = (int)s.size();                                        // cast first, always
    for (int i = n - 1; i >= 0; --i) { /* ... */ }

    // (b) operator[] DEFAULT-INSERTS into a map. Checking m[k] creates k.
    unordered_map<int, int> m;
    assert(m.size() == 0);
    if (m[7] == 0) {}                                             // this just inserted {7,0}
    assert(m.size() == 1);                                        // <-- surprise
    assert(m.count(9) == 0 && m.size() == 1);                     // count/find do not insert

    // (c) priority_queue is a MAX-heap by default.
    priority_queue<int> mx;
    mx.push(1); mx.push(5);
    assert(mx.top() == 5);

    // (d) Overflow in the midpoint and in products.
    int lo = 2000000000, hi = 2000000001;                         // (lo+hi) overflows int
    assert(lo + (hi - lo) / 2 == 2000000000);                     // safe form
    long long product = 100000LL * 100000LL;                      // 10^10 needs long long
    assert(product == 10000000000LL);

    // (e) accumulate takes its accumulator TYPE from the initialiser.
    vector<int> big(3, 1000000000);
    //   accumulate(big.begin(), big.end(), 0)     <-- 0 is an int: 3e9 overflows, and signed
    //                                                 overflow is UNDEFINED BEHAVIOUR, not a
    //                                                 wrapped number. Never rely on it.
    assert(accumulate(big.begin(), big.end(), 0LL) == 3000000000LL); // 0LL -> long long, correct

    // (f) unordered_map<pair<int,int>, V> does not compile without a hash.
    unordered_map<pair<int, int>, int, PairHash> cellValue;
    cellValue[{1, 2}] = 9;                                     // assert on this is gotcha (m)

    // (g) erasing while iterating a map/vector needs the returned iterator.
    vector<int> v = {1, 2, 3, 4};
    for (auto it = v.begin(); it != v.end(); ) {
        if (*it % 2 == 0) it = v.erase(it); else ++it;            // erase returns the next valid it
    }
    assert((v == vector<int>{1, 3}));

    // (h) set::erase(value) vs erase(iterator): both fine, but lower_bound is
    //     the tool for "smallest element >= x".
    set<int> st{1, 3, 5, 7};
    assert(*st.lower_bound(4) == 5);
    assert(st.lower_bound(9) == st.end());                        // check before dereferencing!

    // (i) tolower/toupper take an int and need an unsigned char for safety.
    char ch = 'A';
    assert(tolower(static_cast<unsigned char>(ch)) == 'a');

    // (j) substr(start, length) -- second argument is LENGTH, not end index.
    assert(string("abcdef").substr(1, 3) == "bcd");

    // (k) Integer division truncates toward zero; ceil needs the +b-1 form.
    assert((7 + 2 - 1) / 2 == 4);                                 // ceil(7/2) without doubles
    assert(-7 / 2 == -3);                                         // truncation, not floor

    // (l) vector<bool> is bit-packed -- `auto& x = v[i]` is a proxy, not a bool&.
    vector<bool> flags(4, false);
    flags[2] = true;
    assert(flags[2] && !flags[0]);

    // (m) assert() is a MACRO. A top-level comma inside it -- from a braced pair,
    //     a template with two args, or a template argument list -- is parsed as an
    //     argument separator and you get "macro passed 2 arguments". Fix: one more
    //     pair of parentheses around the whole expression.
    assert((cellValue[{1, 2}] == 9));                          // NOT assert(cellValue[{1,2}] == 9)

    // (n) pair<int, string> does not compare equal to make_pair(1, "b"): the
    //     literal deduces to const char*, not string. Wrap it: string("b").
    pair<int, string> ps = {1, "b"};                           // brace init converts fine
    assert(ps == make_pair(1, string("b")));                   // explicit string required here
    assert(ps.second == "b");                                  // but comparing .second is fine
}

// ============================================================================
// SELF-TESTS
// ============================================================================

int main() {
    declarations();

    assert((mergeIntervals({{1, 3}, {2, 6}, {8, 10}, {15, 18}})
            == vector<vector<int>>{{1, 6}, {8, 10}, {15, 18}}));
    sortingExtras();
    heaps();

    auto ts = threeSum({-1, 0, 1, 2, -1, -4});
    assert(ts.size() == 2);
    vector<int> heights = {1, 8, 6, 2, 5, 4, 8, 3, 7};
    assert(maxArea(heights) == 49);

    assert(longestUniqueSubstring("abcabcbb") == 3);
    assert(longestUniqueSubstring("pwwkew") == 3);
    assert(longestAtMostKDistinct("eceba", 2) == 3);
    assert(minWindow("ADOBECODEBANC", "ABC") == "BANC");
    assert(minWindow("a", "aa") == "");

    assert(subarraySumK({1, 1, 1}, 2) == 2);
    assert(subarraySumK({1, -1, 0}, 0) == 3);                  // needs the {0:1} seed
    assert((productExceptSelf({1, 2, 3, 4}) == vector<int>{24, 12, 8, 6}));

    vector<int> sorted = {5, 7, 7, 8, 8, 10};
    assert(bsExact(sorted, 8) >= 3);
    assert((firstAndLast(sorted, 8) == pair<int, int>{3, 4}));
    assert((firstAndLast(sorted, 6) == pair<int, int>{-1, -1}));
    assert(lowerIdx(sorted, 6) == 1 && upperIdx(sorted, 8) == 5);
    assert(searchRotated({4, 5, 6, 7, 0, 1, 2}, 0) == 4);
    assert(searchRotated({4, 5, 6, 7, 0, 1, 2}, 3) == -1);
    assert(kokoBananas({3, 6, 7, 11}, 8) == 4);
    assert(kokoBananas({30, 11, 23, 4, 20}, 5) == 30);

    assert((nextGreater({2, 1, 2, 4, 3}) == vector<int>{4, 2, 4, -1, -1}));
    assert((dailyTemperatures({73, 74, 75, 71, 69, 72, 76, 73})
            == vector<int>{1, 1, 4, 2, 1, 1, 0, 0}));

    assert(shortestPathBinaryMatrix({{0, 1}, {1, 0}}) == 2);
    assert(shortestPathBinaryMatrix({{0, 0, 0}, {1, 1, 0}, {1, 1, 0}}) == 4);
    assert(numIslands({{'1', '1', '0'}, {'0', '1', '0'}, {'0', '0', '1'}}) == 2);

    assert(topoOrder(3, {{1}, {2}, {}}).size() == 3);            // 0->1->2, acyclic
    assert(topoOrder(2, {{1}, {0}}).empty());                    // 0<->1, cycle detected
    assert((topoOrder(4, {{1}, {2}, {}, {0}})                    // 3->0->1->2
            == vector<int>{3, 0, 1, 2}));

    DSU dsu(5);
    assert(dsu.unite(0, 1) && dsu.unite(1, 2));
    assert(!dsu.unite(0, 2));                                    // already connected
    assert(dsu.find(0) == dsu.find(2) && dsu.find(3) != dsu.find(0));

    vector<vector<pair<int, int>>> wadj = {{{1, 4}, {2, 1}}, {{3, 1}}, {{1, 2}, {3, 5}}, {}};
    auto dist = dijkstra(4, wadj, 0);
    assert((dist == vector<int>{0, 3, 1, 4}));

    TreeNode n1(1), n2(2), n3(3), n4(4), n5(5);
    n2.left = &n4; n2.right = &n5;
    n1.left = &n2; n1.right = &n3;
    TreeNode* root = &n1;
    assert(maxDepth(root) == 3);
    assert(treeDiameter(root) == 3);                             // 4->2->1->3
    assert(isBst(root, LLONG_MIN, LLONG_MAX) == false);          // {1,2,3,4,5} is not a BST
    assert((levelOrder(root) == vector<vector<int>>{{1}, {2, 3}, {4, 5}}));
    TreeNode b2(2), b1(1), b3(3);
    b2.left = &b1; b2.right = &b3;
    assert(isBst(&b2, LLONG_MIN, LLONG_MAX));

    vector<int> nums = {1, 2, 3};
    vector<int> cur; vector<vector<int>> out;
    subsetsHelper(nums, 0, cur, out);
    assert(out.size() == 8);                                     // 2^3
    cur.clear(); out.clear();
    vector<bool> used(3, false);
    permuteHelper(nums, used, cur, out);
    assert(out.size() == 6);                                     // 3!
    out.clear();
    vector<int> cand = {1, 1, 2, 5, 6, 7, 10};                   // sorted, with a duplicate
    combHelper(cand, 0, 8, cur, out);
    for (const auto& combo : out) {
        int sum = accumulate(combo.begin(), combo.end(), 0);
        assert(sum == 8);
    }
    assert(!out.empty());

    assert(coinChange({1, 2, 5}, 11) == 3);
    assert(coinChange({2}, 3) == -1);
    assert(longestCommonSubsequence("abcde", "ace") == 3);
    assert(knapsack01({1, 3, 4, 5}, {1, 4, 5, 7}, 7) == 9);
    assert(memoisedWithLambda(30) == 832040);

    Trie trie;
    trie.insert("apple");
    assert(trie.search("apple") && !trie.search("app"));
    assert(trie.startsWith("app") && !trie.startsWith("bx"));

    LRUCache lru(2);
    lru.put(1, 1); lru.put(2, 2);
    assert(lru.get(1) == 1);                                     // 2 becomes LRU
    lru.put(3, 3);                                               // evicts 2
    assert(lru.get(2) == -1);
    lru.put(4, 4);                                               // evicts 1
    assert(lru.get(1) == -1 && lru.get(3) == 3 && lru.get(4) == 4);

    gotchas();

    cout << "all templates compile and pass\n";
    return 0;
}
