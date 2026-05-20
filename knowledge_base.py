import json
import math
import re
from collections import Counter


class KnowledgeBase:
    def __init__(self):
        self.chunks = []
        self.index = {}  # token -> {chunk_id: freq}
        self.idf = {}
        self.avgdl = 0
        self.k1 = 1.5
        self.b = 0.75

    def load_from_file(self, path: str):
        with open(path, encoding="utf-8") as f:
            self.chunks = json.load(f)
        self._build_index()

    def _tokenize(self, text: str) -> list[str]:
        # 中文字级别 + 英文词级别
        text = text.lower()
        chars = list(re.sub(r"\s+", " ", text))
        tokens = []
        word = ""
        for c in chars:
            if "一" <= c <= "鿿":
                if word:
                    tokens.append(word)
                    word = ""
                tokens.append(c)
            elif c.isalnum() or c == "_":
                word += c
            else:
                if word:
                    tokens.append(word)
                    word = ""
        if word:
            tokens.append(word)
        # bigrams for better Chinese matching
        bigrams = [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)
                   if "一" <= tokens[i] <= "鿿" and "一" <= tokens[i + 1] <= "鿿"]
        return tokens + bigrams

    def _build_index(self):
        dl = []
        tf_per_doc = []
        for chunk in self.chunks:
            tokens = self._tokenize(chunk["content_text"])
            dl.append(len(tokens))
            tf_per_doc.append(Counter(tokens))

        self.avgdl = sum(dl) / max(len(dl), 1)
        N = len(self.chunks)

        # IDF
        df = Counter()
        for tf in tf_per_doc:
            for token in tf:
                df[token] += 1
        self.idf = {t: math.log((N - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}

        # Inverted index: token -> list of (chunk_id, tf)
        self.index = {}
        for cid, (tf, d) in enumerate(zip(tf_per_doc, dl)):
            for token, freq in tf.items():
                score = (self.idf.get(token, 0) * freq * (self.k1 + 1) /
                         (freq + self.k1 * (1 - self.b + self.b * d / self.avgdl)))
                if token not in self.index:
                    self.index[token] = {}
                self.index[token][cid] = score

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        tokens = self._tokenize(query)
        scores = Counter()
        for token in tokens:
            if token in self.index:
                for cid, score in self.index[token].items():
                    scores[cid] += score

        results = []
        for cid, score in scores.most_common(top_k):
            chunk = self.chunks[cid].copy()
            chunk["score"] = round(score, 3)
            results.append(chunk)
        return results
