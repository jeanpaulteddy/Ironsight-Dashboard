# backend/state.py
from dataclasses import dataclass, field
from typing import List, Dict, Any

from config import ARROWS_PER_END, MAX_ENDS

@dataclass
class Shot:
    ts: float
    x: float
    y: float
    r: float
    score: int
    is_x: bool

@dataclass
class SessionState:
    ends: List[List[Shot]] = field(default_factory=list)

    def add_shot(self, shot: Shot):
        if not self.ends:
            self.ends.append([])

        if len(self.ends[-1]) >= ARROWS_PER_END:
            if len(self.ends) < MAX_ENDS:
                self.ends.append([])
            else:
                # If max ends reached, start overwriting the last end
                self.ends[-1] = []

        self.ends[-1].append(shot)

    def to_payload(self) -> Dict[str, Any]:
        running_total = 0
        ends_payload = []

        # counts for summary
        counts = {"X": 0, 10:0, 9:0, 8:0, 7:0, 6:0, 5:0, 4:0, 3:0, 2:0, 1:0, 0:0}

        for i, end in enumerate(self.ends, start=1):
            row_scores = []
            end_sum = 0

            for shot in end:
                row_scores.append("X" if shot.is_x else shot.score)
                end_sum += shot.score

                if shot.is_x:
                    counts["X"] += 1
                    counts[10] += 1  # optionally count X as a 10 too
                else:
                    counts[shot.score] = counts.get(shot.score, 0) + 1

            running_total += end_sum

            ends_payload.append({
                "end": i,
                "arrows": row_scores,
                "score": end_sum,
                "running": running_total,
            })

        total_arrows = sum(len(e) for e in self.ends)

        return {
            "ends": ends_payload,
            "counts": counts,
            "total": running_total,
            "total_arrows": total_arrows,
            "arrows_per_end": ARROWS_PER_END,
        }
    
    def all_shots(self):
        out = []
        for end in self.ends:
            for shot in end:
                out.append({
                    "ts": shot.ts,
                    "x": shot.x,
                    "y": shot.y,
                    "r": shot.r,
                    "score": "X" if shot.is_x else shot.score,
                })
        return out
