"""予定（`Agents/SCHEDULE.md`）を読む。人が Obsidian で表を直せば、次に読んだ時から効く。

    # 毎週
    | 曜日 | 始まり | 終わり | 予定 |
    | 月 | 09:00 | 10:30 | 1限 語学 |

    # 臨時
    | 日付 | 始まり | 終わり | 予定 |
    | 2026-11-10 | 18:00 | 22:00 | 代わりのシフト |
    | 2026-11-12 |  |  | 休み: 1限 語学 |

- 時刻は分で持つ。「24:00」まで書ける
- 臨時の行は、その日の予定に足す。予定が「休み: 名前」なら、その日の毎週の同じ名前の予定を消す
- 始まりと終わりが空の臨時の行は、終日の予定として扱う
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WDAY = "月火水木金土日"
DAY_START_HOUR = 4    # 1日の区切りは朝4時（深夜の会話を前日に数える）
OFF = re.compile(r"^休み[:：]\s*(.+)$")


@dataclass(frozen=True)
class Item:
    start: int     # 0時からの分。終日なら 0
    end: int       # 終日なら 1440
    name: str

    @property
    def all_day(self):
        return self.start == 0 and self.end == 24 * 60

    def text(self):
        if self.all_day:
            return self.name
        return f"{self.start // 60:02d}:{self.start % 60:02d}〜{self.end // 60:02d}:{self.end % 60:02d} {self.name}"


def _minutes(s):
    s = s.strip()
    if not s:
        return None
    h, m = s.replace("：", ":").split(":")
    return int(h) * 60 + int(m)


def _rows(lines):
    """md の表の行を [セル, ...] で。見出し行と区切り行は飛ばす。"""
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        out.append(cells)
    return out[1:] if out else []   # 1行目は見出し


class Schedule:
    def __init__(self, weekly=None, extra=None):
        self.weekly = weekly or {}   # 曜日（月=0） -> [Item]
        self.extra = extra or {}     # date -> [Item]

    @classmethod
    def parse(cls, text):
        sections, cur = {}, None
        for ln in text.split("\n"):
            if ln.startswith("#"):
                cur = ln.lstrip("#").strip()
                sections[cur] = []
            elif cur:
                sections[cur].append(ln)
        weekly, extra = {}, {}
        for cells in _rows(sections.get("毎週", [])):
            if len(cells) < 4 or cells[0][:1] not in WDAY:
                continue
            s, e = _minutes(cells[1]), _minutes(cells[2])
            if s is None or e is None:
                continue
            weekly.setdefault(WDAY.index(cells[0][:1]), []).append(Item(s, e, cells[3]))
        for cells in _rows(sections.get("臨時", [])):
            if len(cells) < 4:
                continue
            try:
                d = date.fromisoformat(cells[0])
            except ValueError:
                continue
            s, e = _minutes(cells[1]), _minutes(cells[2])
            extra.setdefault(d, []).append(Item(s or 0, 24 * 60 if e is None else e, cells[3]))
        for items in list(weekly.values()) + list(extra.values()):
            items.sort(key=lambda x: x.start)
        return cls(weekly, extra)

    def items_on(self, d):
        """その日の予定（毎週＋臨時。「休み: 名前」で毎週の分を消す）。"""
        extra = self.extra.get(d, [])
        off = {m.group(1).strip() for m in (OFF.match(x.name) for x in extra) if m}
        items = [x for x in self.weekly.get(d.weekday(), []) if x.name not in off]
        items += [x for x in extra if not OFF.match(x.name)]
        return sorted(items, key=lambda x: x.start)

    def current(self, t):
        """t に入っている予定（終日の予定は除く）。無ければ None。"""
        m = t.hour * 60 + t.minute
        return next((x for x in self.items_on(t.date()) if not x.all_day and x.start <= m < x.end), None)

    def day_text(self, d):
        return "、".join(x.text() for x in self.items_on(d)) or "予定なし"

    def week_text(self, t):
        """この先1週間の予定（朝4時区切り）。"""
        d0 = (t - timedelta(hours=DAY_START_HOUR)).date()
        return "／".join(f"{WDAY[(d0 + timedelta(days=i)).weekday()]}: {self.day_text(d0 + timedelta(days=i))}"
                        for i in range(7))


DAY_PARTS = [(4, "深夜"), (7, "早朝"), (10, "朝"), (12, "昼前"), (15, "昼"), (18, "夕方"), (23, "夜"), (24, "深夜")]


def now_text(t: datetime):
    """LLM に渡す「今」。時間帯の言葉つき（minato 0.15.0。13時に夜のつもりで話した）。"""
    part = next(name for end, name in DAY_PARTS if t.hour < end)
    s = f"{t.month}月{t.day}日（{WDAY[t.weekday()]}）の{part} {t:%H:%M}"
    if t.hour < DAY_START_HOUR:
        s += f"（{WDAY[(t - timedelta(days=1)).weekday()]}曜の夜の続き）"
    return s


def ago(sec):
    """発言からの経過を言葉にする（LLM に時刻の差を計算させない。minato の decide.ago）。"""
    m = int(sec // 60)
    if m < 1:
        return "今"
    if m < 60:
        return f"{m}分前"
    if m < 60 * 24:
        return f"{m // 60}時間前"
    return f"{m // (60 * 24)}日前"
