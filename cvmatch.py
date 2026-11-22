"""CV matching for jobradar: read your CV, list the skills in it, and score each job by how many of the
skills the posting asks for are on your CV. That is the keyword-coverage check that "ATS score" tools
(Jobscan and similar) show; real applicant tracking systems don't publish a score.

  python cvmatch.py cv.pdf                  show the skills found in your CV
  python cvmatch.py cv.pdf posting.txt      score one posting against it
  python jobradar.py --cv cv.pdf ...        score every job in a run (cv.pdf/.docx/.txt/.md next to
                                            jobradar.py is picked up without the flag)

Reads .txt, .md and .docx with the standard library. PDFs need `pypdf` (pip install pypdf) or the
`pdftotext` command; without either, save the CV as .docx or .txt.
"""

import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

CV_NAMES = ("cv.pdf", "cv.docx", "cv.txt", "cv.md", "resume.pdf", "resume.docx", "resume.txt", "resume.md")

# Canonical skill -> pattern. Patterns are matched case-insensitively against whole words, so "go"
# needs context ("golang", "go developer") to avoid matching the verb.
SKILLS = {
    # languages
    "Python": r"python", "Java": r"java(?!\s*script)", "Go": r"golang|go (?:developer|engineer|services?|microservices)|\bin go\b",
    "JavaScript": r"javascript|\bjs\b", "TypeScript": r"typescript", "Kotlin": r"kotlin", "Swift": r"swift",
    "Rust": r"rust", "C#": r"c#|\.net|dotnet", "C++": r"c\+\+", "PHP": r"php", "Ruby": r"ruby", "Scala": r"scala",
    "Elixir": r"elixir", "SQL": r"sql", "Bash": r"bash|shell scripting",
    # web & mobile
    "React": r"react(?:\.?js)?(?! native)", "React Native": r"react native", "Angular": r"angular",
    "Vue": r"vue(?:\.?js)?", "Next.js": r"next\.?js", "Node.js": r"node(?:\.?js)?", "Express": r"express\.?js",
    "Ionic": r"ionic", "Flutter": r"flutter", "HTML/CSS": r"html|css|tailwind", "GraphQL": r"graphql",
    "REST APIs": r"rest(?:ful)?(?: apis?| services)|restful", "gRPC": r"grpc", "Browser extensions": r"(?:browser|chrome) extensions?",
    # backend frameworks
    "Spring Boot": r"spring ?boot|spring framework|spring mvc", "Django": r"django", "Flask": r"flask", "FastAPI": r"fastapi",
    "Rails": r"ruby on rails|rails", "Laravel": r"laravel", "Pydantic": r"pydantic",
    # data & storage
    "PostgreSQL": r"postgres(?:ql)?", "MySQL": r"mysql", "MongoDB": r"mongo(?:db)?", "Redis": r"redis",
    "Elasticsearch": r"elastic ?search|opensearch", "Kafka": r"kafka", "RabbitMQ": r"rabbitmq",
    "Neo4j": r"neo4j", "Spark": r"apache spark|pyspark|spark", "Airflow": r"airflow", "dbt": r"dbt", "Snowflake": r"snowflake",
    "BigQuery": r"bigquery",
    # cloud & ops
    "AWS": r"aws|amazon web services", "GCP": r"gcp|google cloud", "Azure": r"azure", "Docker": r"docker",
    "Kubernetes": r"kubernetes|k8s", "Terraform": r"terraform", "CI/CD": r"ci ?/ ?cd|github actions|gitlab ci|jenkins",
    "Linux": r"linux", "Git": r"git(?!hub|lab)", "Microservices": r"micro-?services",
    # testing
    "Playwright": r"playwright", "Selenium": r"selenium", "Cypress": r"cypress", "Jest": r"jest", "Pytest": r"pytest",
    "Test automation": r"test automation|automated tests?|qa automation",
    # AI
    "LLMs": r"llms?|large language models?", "RAG": r"rag|retrieval[- ]augmented", "LangChain": r"langchain",
    "LangGraph": r"langgraph", "Agents": r"ai agents?|agentic", "Embeddings": r"embeddings?", "Vector DBs": r"vector (?:db|database|search|store)s?|pinecone|weaviate|qdrant|pgvector",
    "OpenAI API": r"openai", "Anthropic API": r"anthropic|claude", "PyTorch": r"pytorch", "TensorFlow": r"tensorflow",
    "Machine learning": r"machine learning|\bml\b", "NLP": r"nlp|natural language processing", "Evals": r"evals?\b|llm evaluation",
    "Voice AI": r"voice ai|speech[- ]to[- ]text|text[- ]to[- ]speech|livekit|deepgram|twilio",
}
_RX = {name: re.compile(r"(?<![\w+#.])(?:" + rx + r")(?![\w+#])", re.I) for name, rx in SKILLS.items()}


@dataclass
class CV:
    path: str
    text: str
    skills: set


# "Go" on its own is a common verb; count it when it sits in a list of skills ("Python, Go, Rust").
_GO_IN_LIST = re.compile(r"(?:^|[,/|•·(;]\s*)go(?=\s*(?:[,/|•·);]|$))", re.I | re.M)


def skills_in(text):
    found = {name for name, rx in _RX.items() if rx.search(text or "")}
    if _GO_IN_LIST.search(text or ""):
        found.add("Go")
    return found


def read_text(path):
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".txt", ".md"):
        return p.read_text(encoding="utf-8", errors="replace")
    if ext == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>|<w:br/>|<w:tab/>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if ext == ".pdf":
        try:
            import pypdf
            return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(str(p)).pages)
        except ImportError:
            pass
        if shutil.which("pdftotext"):
            return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, check=True).stdout
        raise RuntimeError(f"can't read {p.name}: install pypdf (pip install pypdf) or save the CV as .docx or .txt")
    raise RuntimeError(f"can't read {p.name}: use a .pdf, .docx, .txt or .md file")


def load(path):
    text = read_text(path)
    found = skills_in(text)
    if not found:
        raise RuntimeError(f"found no known skills in {path}; is it a scanned image? Try a .txt copy")
    return CV(str(path), text, found)


def find_cv(folder):
    for name in CV_NAMES:
        if (Path(folder) / name).exists():
            return Path(folder) / name
    return None


def match(cv, title, description):
    """(percent, have, missing) over the skills the posting names; percent is None when it names
    fewer than 3, since a score from one or two keywords means little."""
    wanted = skills_in(f"{title}\n{description}")
    have, missing = sorted(wanted & cv.skills), sorted(wanted - cv.skills)
    pct = round(100 * len(have) / len(wanted)) if len(wanted) >= 3 else None
    return pct, have, missing


def apply(job, cv):
    """Set job.cv_match / job.cv_missing and fold the match into job.score (up to +10)."""
    pct, have, missing = match(cv, job.title, job.description)
    job.cv_match = -1 if pct is None else pct
    job.cv_missing = missing
    if pct is not None:
        job.score += round(pct / 10)
        job.why.append(f"CV match {pct}%" + (f", missing {', '.join(missing[:5])}" if missing else ""))


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cv = load(argv[0])
    print(f"{len(cv.skills)} skills found in {argv[0]}:\n  " + ", ".join(sorted(cv.skills)))
    if len(argv) > 1:
        pct, have, missing = match(cv, "", Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
        print(f"\nmatch: {'n/a (posting names fewer than 3 known skills)' if pct is None else f'{pct}%'}")
        print("  have:    " + (", ".join(have) or "-"))
        print("  missing: " + (", ".join(missing) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
