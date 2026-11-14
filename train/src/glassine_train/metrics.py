"""Character and word error rates (edit distance over reference length)."""


def edit(a, b) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def rates(refs: list[str], hyps: list[str]) -> dict:
    ce = sum(edit(r, h) for r, h in zip(refs, hyps))
    cf = sum(edit(r.lower(), h.lower()) for r, h in zip(refs, hyps))
    we = sum(edit(r.split(), h.split()) for r, h in zip(refs, hyps))
    nc = sum(len(r) for r in refs)
    nw = sum(len(r.split()) for r in refs)
    return {"cer": ce / max(nc, 1), "cer_nocase": cf / max(nc, 1), "wer": we / max(nw, 1),
            "line_acc": sum(r == h for r, h in zip(refs, hyps)) / max(len(refs), 1), "lines": len(refs)}


def confusions(refs: list[str], hyps: list[str], top: int = 15) -> list[tuple[str, int]]:
    """Most frequent substitutions, deletions (x>) and insertions (>x) from the alignment."""
    cnt = {}
    for r, h in zip(refs, hyps):
        n, m = len(r), len(h)
        d = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n + 1):
            d[i][0] = i
        for j in range(m + 1):
            d[0][j] = j
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
        i, j = n, m
        while i or j:
            if i and j and d[i][j] == d[i - 1][j - 1] + (r[i - 1] != h[j - 1]):
                if r[i - 1] != h[j - 1]:
                    k = f"{r[i - 1]}>{h[j - 1]}"
                    cnt[k] = cnt.get(k, 0) + 1
                i, j = i - 1, j - 1
            elif i and d[i][j] == d[i - 1][j] + 1:
                k = f"{r[i - 1]}>"
                cnt[k] = cnt.get(k, 0) + 1
                i -= 1
            else:
                k = f">{h[j - 1]}"
                cnt[k] = cnt.get(k, 0) + 1
                j -= 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])[:top]
