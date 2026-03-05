from enum import Enum
from typing import Optional
from pydantic import BaseModel


class FieldType(str, Enum):
    text = "text"
    email = "email"
    tel = "tel"
    url = "url"
    textarea = "textarea"
    select = "select"
    radio = "radio"
    checkbox = "checkbox"
    file = "file"
    hidden = "hidden"


class FieldHints(BaseModel):
    name: Optional[str] = None
    id: Optional[str] = None
    placeholder: Optional[str] = None
    aria_label: Optional[str] = None
    autocomplete: Optional[str] = None


class FormField(BaseModel):
    field_id: str
    label: str
    type: FieldType
    required: bool = False
    selector: str
    options: list[str] = []
    hints: FieldHints = FieldHints()
    step: int = 0


class PageButton(BaseModel):
    role: str        # "next" | "submit" | "back"
    selector: str
    text: str


class FieldInventory(BaseModel):
    url: str
    step: int
    fields: list[FormField]
    buttons: list[PageButton]


class MappingSource(str, Enum):
    heuristic = "heuristic"
    llm = "llm"
    profile = "profile"
    skip = "skip"


class FieldDecision(BaseModel):
    field_id: str
    selector: str
    value: str
    confidence: float
    source: MappingSource
    note: str = ""


class Mapping(BaseModel):
    decisions: list[FieldDecision]
    unfilled_required: list[str] = []


class EEO(BaseModel):
    gender: Optional[str] = None
    ethnicity: Optional[str] = None
    veteran: Optional[str] = None
    disability: Optional[str] = None
    auto_fill: bool = False


class Answers(BaseModel):
    why_company: Optional[str] = None
    why_role: Optional[str] = None
    work_authorization: Optional[str] = None
    visa_required: Optional[str] = None
    start_date: Optional[str] = None
    salary: Optional[str] = None
    application_url: Optional[str] = None
    operator_name: Optional[str] = None
    operator_email: Optional[str] = None
    links: Optional[str] = None
    gdpr_consent: Optional[str] = None


class Files(BaseModel):
    resume: Optional[str] = None
    cover_letter: Optional[str] = None


class Identity(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    website: Optional[str] = None


class Profile(BaseModel):
    identity: Identity
    files: Files = Files()
    answers: Answers = Answers()
    eeo: EEO = EEO()


class ActionRecord(BaseModel):
    seq: int
    type: str          # "fill" | "select" | "check" | "upload" | "click"
    selector: str
    value: str
    step: int
    note: str = ""


class RunResult(BaseModel):
    url: str
    profile: str
    mode: str          # "dry_run" | "live"
    status: str        # "success" | "error" | "partial"
    steps: int
    submitted: bool
    error: Optional[str] = None
    artifacts_dir: str
