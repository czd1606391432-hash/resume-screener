"""工作目录:一个文件夹装下规则、院校清单、待处理的简历和产出。"""
import copy
import json
import shutil
from pathlib import Path

from . import filter as flt

DATA = Path(__file__).resolve().parent / "data"
RULES_FILE = "rules.json"


class ConfigError(Exception):
    pass


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def default_rules():
    return json.loads((DATA / "rules.example.json").read_text(encoding="utf-8"))


class Workspace:
    def __init__(self, root, rules):
        self.root = Path(root)
        self.rules = rules
        f, o = rules["folders"], rules["output"]
        self.inbox, self.passed = self.root / f["inbox"], self.root / f["passed"]
        self.rejected, self.failed = self.root / f["rejected"], self.root / f["failed"]
        self.summary_xlsx, self.log_file = self.root / o["summary_xlsx"], self.root / o["log_file"]
        self._pool = self._aliases = None

    @property
    def pool(self):
        """院校清单 {校名: 分组}。规则里关掉了院校要求就不读文件。"""
        if self._pool is None:
            s = self.rules.get("schools") or {}
            if not s.get("require_in_list"):
                self._pool = {}
            else:
                p = self.root / (s.get("list") or "")
                if not s.get("list") or not p.exists():
                    raise ConfigError("规则要求院校在清单内,但找不到院校清单文件: %s" % p)
                self._pool = flt.load_school_pool(p, s.get("groups") or None,
                                                  int(s.get("xlsx_name_column") or 1), int(s.get("xlsx_first_row") or 2))
                if not self._pool:
                    raise ConfigError("院校清单是空的(或者 groups 里的分组名对不上): %s" % p)
        return self._pool

    @property
    def aliases(self):
        if self._aliases is None:
            name = (self.rules.get("schools") or {}).get("aliases")
            self._aliases = flt.load_aliases(self.root / name) if name else {}
        return self._aliases

    def ensure_dirs(self):
        for d in (self.inbox, self.passed, self.rejected, self.failed, self.summary_xlsx.parent):
            d.mkdir(parents=True, exist_ok=True)


def load_workspace(root="."):
    root = Path(root)
    p = root / RULES_FILE
    if not p.exists():
        raise ConfigError("%s 里没有 %s。先运行: resume-screener init %s" % (root.resolve(), RULES_FILE, root))
    try:
        rules = _merge(default_rules(), json.loads(p.read_text(encoding="utf-8")))
    except ValueError as e:
        raise ConfigError("%s 不是合法的 JSON: %s" % (p, e))
    return Workspace(root, rules)


def init_workspace(root="."):
    """建一个新的工作目录:规则、示例院校清单、示例别名表和各个文件夹。已有的文件不覆盖。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    made = []
    for src, dst in (("rules.example.json", RULES_FILE), ("schools.example.csv", "院校清单.csv"),
                     ("school_aliases.example.json", "校名别名.json")):
        if not (root / dst).exists():
            shutil.copyfile(str(DATA / src), str(root / dst))
            made.append(dst)
    ws = load_workspace(root)
    ws.ensure_dirs()
    return ws, made
