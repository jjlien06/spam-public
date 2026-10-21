"""Skills for multi-pick skill prompts (Workday "Type to Add Skills"): only the person's own skills (from their
profile), with the ones the posting mentions first. KEYWORDS is a generic map from common skills to the words a
posting uses for them and the option text a site shows; a skill it doesn't know is matched by its own name."""
import re

# skill (lowercase) -> (words to look for in the posting, pattern the site's option must match).
# Options must match exactly (with an optional "(Programming Language)"-style suffix): never a
# different skill that merely contains the word.
KEYWORDS = {
    "python": (["python"], r"python"), "c++": (["c\\+\\+"], r"c\+\+"), "java": (["java(?!script)"], r"java"),
    "c": (["c(?![+#\\w])"], r"c"), "c#": (["c#", "\\.net"], r"c#"), "go": (["golang", "\\bgo\\b"], r"go(?:lang)?"),
    "rust": (["rust"], r"rust"), "kotlin": (["kotlin"], r"kotlin"), "swift": (["swift", "\\bios\\b"], r"swift"),
    "ruby": (["ruby"], r"ruby"), "php": (["php"], r"php"), "scala": (["scala"], r"scala"), "r": (["\\br\\b"], r"r"),
    "matlab": (["matlab"], r"matlab"), "sql": (["sql"], r"sql"), "typescript": (["typescript"], r"typescript"),
    "javascript": (["javascript", "\\bjs\\b"], r"javascript"), "html": (["html"], r"html5?"), "css": (["css"], r"css3?"),
    "react": (["react"], r"react(?:\.?js)?"), "angular": (["angular"], r"angular(?:js)?"), "vue": (["vue"], r"vue(?:\.?js)?"),
    "node.js": (["node"], r"node(?:\.?js)?"), "django": (["django"], r"django"), "flask": (["flask"], r"flask"),
    "spring": (["spring"], r"spring(?: boot)?"), "graphql": (["graphql"], r"graphql"), "rest apis": (["rest", "\\bapi"], r"rest(?:ful)? apis?"),
    "pytorch": (["pytorch", "deep learning", "machine learning"], r"pytorch"), "tensorflow": (["tensorflow", "deep learning"], r"tensorflow"),
    "scikit-learn": (["scikit", "sklearn", "machine learning"], r"scikit-learn"), "numpy": (["numpy"], r"numpy"),
    "pandas": (["pandas"], r"pandas"), "scipy": (["scipy"], r"scipy"), "matplotlib": (["matplotlib", "visualization"], r"matplotlib"),
    "opencv": (["opencv", "computer vision"], r"opencv"), "computer vision": (["computer vision", "vision", "image"], r"computer vision"),
    "machine learning": (["machine learning", "\\bml\\b", "\\bai\\b"], r"machine learning"),
    "deep learning": (["deep learning", "neural"], r"deep learning"), "natural language processing": (["nlp", "language model"], r"natural language processing|nlp"),
    "data analysis": (["data analy"], r"data analysis"), "statistics": (["statistic"], r"statistics"),
    "linux": (["linux", "unix"], r"linux"), "git": (["git", "version control"], r"git"), "docker": (["docker", "container"], r"docker"),
    "kubernetes": (["kubernetes", "k8s"], r"kubernetes"), "aws": (["aws", "amazon web services", "cloud"], r"aws|amazon web services"),
    "azure": (["azure"], r"(?:microsoft )?azure"), "gcp": (["gcp", "google cloud"], r"gcp|google cloud(?: platform)?"),
    "bash": (["bash", "shell", "scripting"], r"bash(?: scripting)?"), "powershell": (["powershell"], r"powershell"),
    "postgresql": (["postgres"], r"postgres(?:ql)?"), "mysql": (["mysql"], r"mysql"), "mongodb": (["mongo"], r"mongodb"),
    "redis": (["redis"], r"redis"), "spark": (["spark"], r"(?:apache )?spark"), "kafka": (["kafka"], r"(?:apache )?kafka"),
    "verilog": (["verilog", "rtl", "fpga", "hdl"], r"verilog"), "vhdl": (["vhdl", "hdl"], r"vhdl"),
    "systemverilog": (["systemverilog"], r"systemverilog"), "fpga": (["fpga"], r"fpga(?: design)?"),
    "embedded systems": (["embedded", "firmware", "microcontroller"], r"embedded systems?"), "rtos": (["rtos"], r"rtos"),
    "cuda": (["cuda", "gpu"], r"cuda"), "multithreading": (["multithread", "concurren"], r"multithreading"),
    "parallel computing": (["parallel"], r"parallel computing"), "data structures": (["data structures"], r"data structures"),
    "algorithms": (["algorithms"], r"algorithms"), "object-oriented programming": (["object.oriented", "\\boop\\b"], r"object.oriented programming|oop"),
    "unit testing": (["unit test", "testing"], r"unit testing"), "pytest": (["pytest", "unit test", "testing"], r"pytest"),
    "junit": (["junit"], r"junit"), "ci/cd": (["ci/cd", "continuous integration"], r"ci/?cd"),
    "github actions": (["github actions", "ci/cd", "\\bci\\b"], r"github actions"), "jira": (["jira"], r"jira"),
    "agile": (["agile", "scrum"], r"agile(?: methodolog(?:y|ies))?"), "figma": (["figma"], r"figma"),
    "excel": (["excel", "spreadsheet"], r"(?:microsoft )?excel"), "tableau": (["tableau"], r"tableau"),
    "image processing": (["image processing", "image"], r"image processing"), "electron": (["electron"], r"electron(?:\.?js)?"),
    "technical writing": (["documentation", "technical writing"], r"technical writing"),
}


def choice(search, pattern):
    return {"search": search, "matches": rf"^(?:{pattern})(?: \([^)]*\))?$"}


def ranked(skills, posting="", limit=20):
    """Skill choices for an add-all prompt: this person's skills, those the posting mentions first."""
    text = (posting or "").lower()

    def entry(name):
        words, pattern = KEYWORDS.get(name.lower(), ([re.escape(name.lower())], re.escape(name.lower())))
        return name, words, pattern

    def mentioned(words):
        return any(re.search(rf"(?<![\w+#]){w}(?![\w+#])" if w[0].isalpha() and w[-1].isalpha() else w, text) for w in words)

    entries = [entry(s) for s in dict.fromkeys(s.strip() for s in skills if isinstance(s, str) and s.strip())]
    hits = [e for e in entries if mentioned(e[1])]
    rest = [e for e in entries if e not in hits]
    return {"add_all": [choice(name, pattern) for name, _, pattern in hits + rest], "max": limit}
