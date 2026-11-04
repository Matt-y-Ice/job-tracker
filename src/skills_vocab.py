"""Known tech skills and the patterns used to spot them in free text.

Each entry maps a canonical skill name to the ways it is written.
An empty list means "match the canonical name". Alias prefixes:
  "="   match case-sensitively (for words like Go, Swift, Rust)
  "re:" a raw regular expression
Everything else matches case-insensitively on word boundaries.
"""
import re
from functools import lru_cache

SKILLS: dict[str, list[str]] = {
    # Languages
    "Python": [],
    "Java": [],
    "JavaScript": ["JavaScript", "ECMAScript"],
    "TypeScript": [],
    "C": [r"re:(?<![\w#+.&/-])C(?=\s*,|\s*/\s*C\+\+|\s+(?:programming|language)\b)"],
    "C++": ["C++", "CPP"],
    "C#": ["C#", "C Sharp"],
    "Go": ["Golang", r"re:(?<![\w.])Go(?=\s*[,/)]|\s+(?:programming|language|developer|engineer)\b)"],
    "Rust": ["=Rust"],
    "Ruby": [],
    "PHP": [],
    "Swift": ["=Swift"],
    "Kotlin": [],
    "Scala": [],
    "R": ["RStudio", "R programming", "tidyverse", r"re:(?<![\w&/.-])R(?=\s*,|\s+(?:programming|language)\b)"],
    "SQL": [],
    "Bash": ["Bash", "Shell scripting", "shell scripts"],
    "PowerShell": [],
    "Perl": ["=Perl"],
    "Dart": ["=Dart"],
    "Elixir": [],
    "Erlang": [],
    "Haskell": [],
    "Clojure": [],
    "Lua": ["=Lua"],
    "Julia": ["=Julia"],
    "MATLAB": [],
    "Objective-C": ["Objective-C", "ObjC"],
    "Fortran": [],
    "COBOL": [],
    "Groovy": [],
    "Solidity": [],
    "VB.NET": ["VB.NET", "Visual Basic"],
    "Assembly": ["Assembly language", "x86 assembly", "ARM assembly"],
    "HTML": ["HTML", "HTML5"],
    "CSS": ["CSS", "CSS3", "SCSS", "Sass"],
    # Web and backend frameworks
    "React": ["React", "React.js", "ReactJS"],
    "React Native": [],
    "Angular": ["Angular", "AngularJS"],
    "Vue": ["Vue", "Vue.js", "VueJS"],
    "Svelte": [],
    "Next.js": ["Next.js", "NextJS"],
    "Node.js": ["Node.js", "NodeJS"],
    "Express": ["Express.js", "ExpressJS"],
    "Django": [],
    "Flask": [],
    "FastAPI": [],
    "Spring Boot": ["Spring Boot", "Spring Framework", "Spring MVC"],
    "Ruby on Rails": ["Ruby on Rails", "=Rails"],
    ".NET": [".NET", "ASP.NET", ".NET Core", "dotnet"],
    "jQuery": [],
    "Tailwind CSS": ["Tailwind", "TailwindCSS"],
    "Laravel": [],
    "REST APIs": ["=REST", "RESTful", "REST API", "REST APIs"],
    "GraphQL": [],
    "gRPC": [],
    "WebSockets": ["WebSocket", "WebSockets"],
    "Microservices": ["microservice", "microservices"],
    # Mobile
    "iOS": [],
    "Android": [],
    "SwiftUI": [],
    "Flutter": [],
    # Data stores
    "PostgreSQL": ["PostgreSQL", "Postgres"],
    "MySQL": [],
    "SQLite": [],
    "Oracle": ["Oracle", "Oracle SQL", "PL/SQL"],
    "SQL Server": ["SQL Server", "MSSQL", "T-SQL"],
    "MongoDB": ["MongoDB", "Mongo"],
    "Redis": [],
    "Elasticsearch": ["Elasticsearch", "OpenSearch"],
    "DynamoDB": [],
    "Cassandra": [],
    "ClickHouse": [],
    "Neo4j": [],
    "Snowflake": ["=Snowflake"],
    "BigQuery": [],
    # Cloud and DevOps
    "AWS": ["AWS", "Amazon Web Services", "EC2", "=S3", "AWS Lambda"],
    "Azure": ["Azure", "Microsoft Azure"],
    "GCP": ["GCP", "Google Cloud"],
    "Docker": [],
    "Kubernetes": ["Kubernetes", "k8s"],
    "Terraform": [],
    "Ansible": [],
    "Jenkins": [],
    "GitHub Actions": [],
    "CI/CD": ["CI/CD", "continuous integration", "continuous delivery", "continuous deployment"],
    "Git": [],
    "GitHub": [],
    "GitLab": [],
    "Linux": ["Linux", "Ubuntu", "Debian", "RHEL", "CentOS"],
    "Unix": [],
    "Nginx": [],
    "Serverless": [],
    "Grafana": [],
    "Prometheus": [],
    "Tailscale": [],
    # Data engineering and analytics
    "Spark": ["=Spark", "PySpark", "Apache Spark"],
    "Kafka": ["Kafka", "Apache Kafka"],
    "Airflow": ["Airflow", "Apache Airflow"],
    "dbt": ["=dbt"],
    "Databricks": [],
    "Hadoop": [],
    "ETL": ["ETL", "ELT"],
    "Data Pipelines": ["data pipeline", "data pipelines"],
    "pandas": [],
    "NumPy": [],
    "Excel": ["=Excel", "Microsoft Excel"],
    "Tableau": [],
    "Power BI": ["Power BI", "PowerBI"],
    # Machine learning
    "Machine Learning": ["machine learning"],
    "Deep Learning": ["deep learning"],
    "Computer Vision": ["computer vision"],
    "NLP": ["NLP", "natural language processing"],
    "LLMs": ["LLM", "LLMs", "large language model", "large language models"],
    "PyTorch": [],
    "TensorFlow": [],
    "Keras": [],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "OpenCV": [],
    "Hugging Face": ["Hugging Face", "HuggingFace", "transformers library"],
    "CNNs": ["CNN", "CNNs", "convolutional neural network", "convolutional neural networks"],
    "Image Segmentation": ["image segmentation", "semantic segmentation", "instance segmentation"],
    "MLOps": [],
    # Testing and practices
    "Unit Testing": ["unit test", "unit tests", "unit testing", "unit-tested", "unit-test"],
    "pytest": [],
    "JUnit": [],
    "Jest": ["=Jest"],
    "Selenium": [],
    "Cypress": [],
    "Playwright": [],
    "TDD": ["TDD", "test-driven development", "test driven development"],
    "Agile": ["=Agile", "agile methodology"],
    "Scrum": [],
    "OOP": ["OOP", "object-oriented programming", "object-oriented"],
    "Distributed Systems": ["distributed system", "distributed systems"],
    "System Design": ["system design"],
    "API Integration": ["API integration", "API integrations"],
    "Automation": ["automation", "workflow automation"],
    # Python tooling and misc
    "Tkinter": [],
    "openpyxl": [],
    "PyMuPDF": [],
    "argparse": [],
    "Regex": ["regex", "regular expressions"],
    "CLI Tools": ["CLI", "command-line tool", "command line tool"],
    "Jira": [],
    "SharePoint": [],
    "AutoCAD": [],
    "Mapbox": [],
    "Carto": ["=Carto"],
    "SCADA": [],
    "PLC": ["PLC", "PLCs"],
    "ROS": ["=ROS", "Robot Operating System"],
    "Embedded Systems": ["embedded systems", "embedded software", "firmware"],
    "RTOS": [],
    "FPGA": [],
    "Verilog": [],
    "LabVIEW": [],
    "Simulink": [],
    "OAuth": ["OAuth", "OAuth2", "OIDC"],
    "Security": ["cybersecurity", "application security", "AppSec"],
}

_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9+#])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9+#])"


def _alias_pattern(alias: str) -> re.Pattern:
    """Compile one alias into a regex following the prefix rules above."""
    if alias.startswith("re:"):
        return re.compile(alias[3:])
    if alias.startswith("="):
        return re.compile(_BOUNDARY_BEFORE + re.escape(alias[1:]) + _BOUNDARY_AFTER)
    return re.compile(_BOUNDARY_BEFORE + re.escape(alias) + _BOUNDARY_AFTER, re.IGNORECASE)


@lru_cache(maxsize=1)
def compiled_skills() -> list[tuple[str, list[re.Pattern]]]:
    """Return (canonical name, patterns) pairs, compiled once."""
    return [
        (name, [_alias_pattern(alias) for alias in (aliases or [name])])
        for name, aliases in SKILLS.items()
    ]


def find_skills(text: str) -> list[str]:
    """Return canonical names of every known skill mentioned in `text`."""
    return [
        name
        for name, patterns in compiled_skills()
        if any(pattern.search(text) for pattern in patterns)
    ]


def known_alias_keys() -> set[str]:
    """Lower-cased canonical names and plain aliases, for de-duplication."""
    keys = {name.lower() for name in SKILLS}
    for aliases in SKILLS.values():
        keys.update(a.lstrip("=").lower() for a in aliases if not a.startswith("re:"))
    return keys
