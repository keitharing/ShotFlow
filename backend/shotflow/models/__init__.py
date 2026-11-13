from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VersionStatus:
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION = "revision_requested"
    ALL = (PENDING, IN_REVIEW, APPROVED, REJECTED, REVISION)


class Origin:
    GENERATED = "generated"
    FIX = "fix"
    EDIT = "edit"
    FX = "fx"
    IMPORTED = "imported"
    BLENDER = "blender"


class Severity:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    fps: Mapped[float] = mapped_column(Float, default=24.0)
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    target_lufs: Mapped[float] = mapped_column(Float, default=-16.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sequences: Mapped[list[Sequence]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Sequence.order"
    )


class Sequence(Base):
    __tablename__ = "sequence"
    __table_args__ = (UniqueConstraint("project_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    order: Mapped[int] = mapped_column(Integer, default=0)
    target_duration_s: Mapped[float | None] = mapped_column(Float)
    # Own delivery size for this film; empty means "same as the project" (week 7).
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    project: Mapped[Project] = relationship(back_populates="sequences")
    shots: Mapped[list[Shot]] = relationship(
        back_populates="sequence", cascade="all, delete-orphan", order_by="Shot.order"
    )
    edits: Mapped[list[Edit]] = relationship(
        back_populates="sequence", cascade="all, delete-orphan", order_by="Edit.id"
    )

    @property
    def frame_size(self) -> tuple[int, int]:
        """Delivery size used by QC and the edit render: the film's own size, else the project's."""
        if self.width and self.height:
            return self.width, self.height
        return self.project.width, self.project.height


class Shot(Base):
    """A shot in a sequence. kind='edit' holds the assembled cuts of the whole sequence."""

    __tablename__ = "shot"
    __table_args__ = (UniqueConstraint("sequence_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="shot")
    order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    approved_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("version.id", use_alter=True, name="fk_shot_approved_version", ondelete="SET NULL")
    )

    sequence: Mapped[Sequence] = relationship(back_populates="shots")
    versions: Mapped[list[Version]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        foreign_keys="Version.shot_id",
        order_by="Version.id",
    )
    approved_version: Mapped[Version | None] = relationship(
        foreign_keys=[approved_version_id], post_update=True
    )


class Version(Base):
    __tablename__ = "version"
    __table_args__ = (UniqueConstraint("shot_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shot_id: Mapped[int] = mapped_column(ForeignKey("shot.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("version.id", ondelete="SET NULL"))
    origin: Mapped[str] = mapped_column(String(16), default=Origin.IMPORTED)
    status: Mapped[str] = mapped_column(String(24), default=VersionStatus.PENDING)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    sha256_expected: Mapped[str | None] = mapped_column(String(64))
    bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(32))
    has_audio: Mapped[bool | None] = mapped_column()
    generation_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_job.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    shot: Mapped[Shot] = relationship(back_populates="versions", foreign_keys=[shot_id])
    parent: Mapped[Version | None] = relationship(remote_side="Version.id")
    generation_job: Mapped[GenerationJob | None] = relationship()
    notes: Mapped[list[Note]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="Note.id"
    )
    qc_runs: Mapped[list[QCRun]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="QCRun.id"
    )

    @property
    def sha_verified(self) -> bool | None:
        if not self.sha256_expected:
            return None
        return self.sha256 == self.sha256_expected


class Recipe(Base):
    __tablename__ = "recipe"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # keyframe, img2img, inpaint, t2v, v2v
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    versions: Mapped[list[RecipeVersion]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeVersion.number"
    )


class RecipeVersion(Base):
    __tablename__ = "recipe_version"
    __table_args__ = (UniqueConstraint("recipe_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    api_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    ui_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    exposed_inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    required_models: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    author: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    recipe: Mapped[Recipe] = relationship(back_populates="versions")


class GenerationJob(Base):
    __tablename__ = "generation_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))  # comfyui, higgsfield
    model: Mapped[str | None] = mapped_column(String(120))
    recipe_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipe_version.id", ondelete="SET NULL")
    )
    batch_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seed: Mapped[int | None] = mapped_column(Integer)
    mask_path: Mapped[str | None] = mapped_column(Text)
    # The frozen prompt this job was made from, when it came from prompt management (week 8).
    prompt_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    credits: Mapped[float | None] = mapped_column(Float)
    peak_vram_mb: Mapped[float | None] = mapped_column(Float)
    peak_temp_c: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    source_record: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class QCRun(Base):
    __tablename__ = "qc_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))  # pass, warning, error, failed
    measurements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_s: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    version: Mapped[Version] = relationship(back_populates="qc_runs")
    issues: Mapped[list[QCIssue]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="QCIssue.start_frame"
    )


class QCIssue(Base):
    __tablename__ = "qc_issue"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("qc_run.id", ondelete="CASCADE"))
    check: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    start_frame: Mapped[int | None] = mapped_column(Integer)
    end_frame: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text)

    run: Mapped[QCRun] = relationship(back_populates="issues")


class Note(Base):
    __tablename__ = "note"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id", ondelete="CASCADE"))
    author: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    frame: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str | None] = mapped_column(String(24))  # approve, reject, revise, comment
    source: Mapped[str] = mapped_column(String(24), default="user")  # user, imported
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    version: Mapped[Version] = relationship(back_populates="notes")


class Edit(Base):
    """An edit decision list for one sequence; result_version is the assembled movie."""

    __tablename__ = "edit"
    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    result_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("version.id", ondelete="SET NULL")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sequence: Mapped[Sequence] = relationship(back_populates="edits")
    entries: Mapped[list[EditEntry]] = relationship(
        back_populates="edit", cascade="all, delete-orphan", order_by="EditEntry.order"
    )
    result_version: Mapped[Version | None] = relationship()


class EditEntry(Base):
    __tablename__ = "edit_entry"
    id: Mapped[int] = mapped_column(primary_key=True)
    edit_id: Mapped[int] = mapped_column(ForeignKey("edit.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id"))
    src_in: Mapped[int] = mapped_column(Integer)
    src_out: Mapped[int] = mapped_column(Integer)  # exclusive
    transition: Mapped[str] = mapped_column(String(16), default="cut")
    transition_frames: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    edit: Mapped[Edit] = relationship(back_populates="entries")
    version: Mapped[Version] = relationship()

    @property
    def frames(self) -> int:
        return self.src_out - self.src_in


class Delivery(Base):
    __tablename__ = "delivery"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    naming_rule: Mapped[str] = mapped_column(String(200))
    target_dir: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    items: Mapped[list[DeliveryItem]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )


class DeliveryItem(Base):
    __tablename__ = "delivery_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("delivery.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id"))
    file_name: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    delivery: Mapped[Delivery] = relationship(back_populates="items")


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PromptBlock(Base):
    """A piece of prompt text shared by the shots of one film: the look, a palette, things to avoid (week 8)."""

    __tablename__ = "prompt_block"
    __table_args__ = (UniqueConstraint("sequence_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    place: Mapped[str] = mapped_column(String(16), default="after")  # before | after the scene text, or negative
    order: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ShotPrompt(Base):
    """What is special about one shot (the scene text) and which shared blocks it uses, for one use."""

    __tablename__ = "shot_prompt"
    __table_args__ = (UniqueConstraint("shot_id", "use"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shot_id: Mapped[int] = mapped_column(ForeignKey("shot.id", ondelete="CASCADE"))
    use: Mapped[str] = mapped_column(String(16), default="image")  # image | image_local | video
    scene: Mapped[str] = mapped_column(Text, default="")
    block_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    shot: Mapped[Shot] = relationship()


class PromptSnapshot(Base):
    """The exact text handed to a generation. Never edited: a different text is a different snapshot."""

    __tablename__ = "prompt_snapshot"
    __table_args__ = (UniqueConstraint("shot_id", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shot_id: Mapped[int] = mapped_column(ForeignKey("shot.id", ondelete="CASCADE"))
    use: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    negative: Mapped[str | None] = mapped_column(Text)
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Animatic(Base):
    """A storyboard animatic of one film, split into cuts. Cuts with a drawing can be turned into Blender blocking."""

    __tablename__ = "animatic"
    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequence.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float] = mapped_column(Float)
    frame_count: Mapped[int] = mapped_column(Integer)
    # Drawing area inside the frame [x, y, w, h] in pixels (storyboard pages put the drawing on a dark page)
    crop: Mapped[list[int]] = mapped_column(JSON)
    # [{index, start, end, has_panel, shot_id, frames: {start, end}}], end exclusive
    cuts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sequence: Mapped[Sequence] = relationship()


class ShotLayout(Base):
    """Blocking of one shot made from marks on an animatic drawing: boxes, horizon, lens -> camera and proxies."""

    __tablename__ = "shot_layout"
    __table_args__ = (UniqueConstraint("shot_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shot_id: Mapped[int] = mapped_column(ForeignKey("shot.id", ondelete="CASCADE"))
    animatic_id: Mapped[int | None] = mapped_column(ForeignKey("animatic.id", ondelete="SET NULL"))
    cut_index: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(32))
    marks: Mapped[dict[str, Any]] = mapped_column(JSON)       # what the artist (or a suggestion) marked
    solution: Mapped[dict[str, Any]] = mapped_column(JSON)    # cameras and proxies computed from the marks
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | suggested | mixed
    generation_job_id: Mapped[int | None] = mapped_column(ForeignKey("generation_job.id", ondelete="SET NULL"))
    blend_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    shot: Mapped[Shot] = relationship()
    animatic: Mapped[Animatic | None] = relationship()
    generation_job: Mapped[GenerationJob | None] = relationship()


def log_activity(session, actor: str, action: str, target=None, **detail: Any) -> ActivityLog:
    entry = ActivityLog(
        actor=actor,
        action=action,
        target_type=type(target).__name__.lower() if target is not None else None,
        target_id=getattr(target, "id", None),
        detail=detail,
    )
    session.add(entry)
    return entry
