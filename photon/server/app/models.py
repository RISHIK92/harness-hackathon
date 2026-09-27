import secrets
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional
from sqlmodel import Field, SQLModel, Column, JSON, Relationship
from sqlalchemy import String, ForeignKey, Integer


# ─── User ─────────────────────────────────────────────────────────────────────

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    email: str = Field(sa_column=Column(String, unique=True, nullable=False, index=True))
    # Nullable: a user who signs up via "Continue with GitHub" has no
    # password at all. core/auth.py's password-login path must check for
    # None before calling verify_password, not pass it a None hash.
    hashed_password: Optional[str] = None
    # Set when signed up/linked via GitHub OAuth (routers/auth.py). Linking
    # rule: match on github_id first, then fall back to matching the
    # verified GitHub email against an existing User.email (so a user who
    # signed up with email/password and later clicks "Continue with
    # GitHub" gets linked to their existing account instead of a duplicate).
    github_id: Optional[str] = Field(default=None, sa_column=Column(String, unique=True, nullable=True))
    github_login: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserCreate(SQLModel):
    email: str
    password: str


class UserRead(SQLModel):
    id: str
    email: str
    created_at: datetime


# ─── Workspace (the tenant) ───────────────────────────────────────────────────
# Everything a customer owns hangs off a workspace, not off a user: repos
# today, and Slack/email connectors, agents and transcripts next. A user is
# a login; a workspace is the thing that HAS data and can be shared. This is
# in from the start deliberately — retrofitting a tenant boundary under live
# data is far more expensive than carrying it now.


class WorkspaceKind(str, Enum):
    """What a workspace IS, chosen when it is created.

    INDIVIDUAL — one person's own context. No invites, no members to
                 manage; the connectors and documents in it are theirs.
    TEAM       — shared. Members are invited and approved, and every
                 connector added to it is readable by everyone in it.

    The distinction is asked up front rather than inferred, because the
    answer changes what "connect Slack" MEANS: in a team workspace it
    exposes that Slack to everyone who is ever admitted, and someone
    connecting a personal account should know which of those they are doing.
    """
    INDIVIDUAL = "individual"
    TEAM = "team"


class WorkspaceRole(str, Enum):
    """Ordered least- to most-privileged; see core/workspace.py:role_at_least.

    VIEWER  — ask questions, join calls, read transcripts. Cannot change
              what the workspace knows.
    MEMBER  — plus import repos and manage what is indexed.
    OWNER   — plus connect/disconnect integrations, invite and remove people.
    """
    VIEWER = "viewer"
    MEMBER = "member"
    OWNER = "owner"


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    kind: WorkspaceKind = WorkspaceKind.INDIVIDUAL
    # What the agent is called on this workspace's calls. Null means the
    # product default. Worth having: people address it out loud, and "Ask
    # Photon" is a stranger's name in someone else's company.
    agent_name: Optional[str] = None
    # True for the workspace auto-created on signup, so the UI can label it
    # and never offer to delete a user's only home.
    is_personal: bool = False
    # The company-level agent: when an owner turns this on, calls may be
    # attended on behalf of the company (Meeting.attends_as == "company") and
    # unowned tickets are triaged by the org agent instead of being ignored.
    org_agent_enabled: bool = False
    # Which source groups the company agent may draw on. Null means every
    # workspace-shared source; a list narrows it (e.g. docs + GitHub, never
    # Slack) — the admin's scope for what "the company" says on a call.
    org_agent_sources: Optional[list] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceMember(SQLModel, table=True):
    __tablename__ = "workspace_members"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    role: WorkspaceRole = WorkspaceRole.MEMBER
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceInvite(SQLModel, table=True):
    """One live invite code per workspace at a time.

    Rotating replaces rather than accumulates: an owner who suspects a code
    has leaked needs "make the old one stop working" to be one obvious
    action, not a list to audit.
    """
    __tablename__ = "workspace_invites"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    code: str = Field(sa_column=Column(String, unique=True, nullable=False, index=True))
    created_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = None


class JoinRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkspaceJoinRequest(SQLModel, table=True):
    """A code gets you as far as asking. An owner decides.

    Holding the code is not the same as being trusted with a company's
    private source, so the code proves you were pointed at this workspace,
    and approval is what actually grants access.
    """
    __tablename__ = "workspace_join_requests"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    status: JoinRequestStatus = JoinRequestStatus.PENDING
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    granted_role: Optional[WorkspaceRole] = None


class WorkspaceMemberRead(SQLModel):
    user_id: str
    email: str
    role: WorkspaceRole
    joined_at: datetime


class JoinRequestRead(SQLModel):
    id: str
    user_id: str
    email: str
    status: JoinRequestStatus
    requested_at: datetime


class WorkspaceCreate(SQLModel):
    name: str
    kind: WorkspaceKind = WorkspaceKind.TEAM
    agent_name: Optional[str] = None


class WorkspaceUpdate(SQLModel):
    name: Optional[str] = None
    agent_name: Optional[str] = None
    org_agent_enabled: Optional[bool] = None
    org_agent_sources: Optional[list[str]] = None


class WorkspaceRead(SQLModel):
    id: str
    name: str
    kind: WorkspaceKind = WorkspaceKind.INDIVIDUAL
    # What the agent is called on this workspace's calls. Null means the
    # product default. Worth having: people address it out loud, and "Ask
    # Photon" is a stranger's name in someone else's company.
    agent_name: Optional[str] = None
    is_personal: bool
    org_agent_enabled: bool = False
    org_agent_sources: Optional[list] = None
    role: WorkspaceRole
    created_at: datetime


# ─── GitHub App installations ─────────────────────────────────────────────────
# One row per "org/user installed the GitHub App and granted it access to
# some repos", scoped to the workspace that installed it. Created by
# routers/github_app.py's install callback; read by the repo picker to list
# what's visible, and by tasks/ingestion.py to know which installation's
# token to mint for cloning. See app/services/github_app_auth.py.

class ConnectionScope(str, Enum):
    """Who a connected source belongs to.

    WORKSPACE — shared: every member's questions may draw on it.
    USER      — private to the person who connected it. On a call it is
                only used for turns attributed to that person (see the
                speaker-identity plumbing), never for everyone.
    """
    WORKSPACE = "workspace"
    USER = "user"


# ─── Slack ────────────────────────────────────────────────────────────────


class SlackInstallation(SQLModel, table=True):
    """One Slack workspace connected to one Photon workspace."""
    __tablename__ = "slack_installations"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    scope: ConnectionScope = ConnectionScope.WORKSPACE
    owner_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    team_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    team_name: str
    # Fernet-encrypted; never returned by any endpoint. A bot token reads
    # every channel the app was added to, so it does not sit in plain text.
    bot_token_encrypted: str
    bot_user_id: Optional[str] = None
    installed_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_synced_at: Optional[datetime] = None


class SlackChannel(SQLModel, table=True):
    """A channel the workspace chose to index. Selection is explicit:
    connecting Slack must not silently ingest every channel a bot can see,
    including ones people forgot it was in."""
    __tablename__ = "slack_channels"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    installation_id: str = Field(sa_column=Column(String, ForeignKey("slack_installations.id", ondelete="CASCADE"), nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    channel_id: str
    name: str
    is_private: bool = False
    selected: bool = True
    message_count: int = 0
    last_synced_at: Optional[datetime] = None


class SlackInstallationRead(SQLModel):
    id: str
    team_id: str
    team_name: str
    scope: ConnectionScope
    created_at: datetime
    last_synced_at: Optional[datetime]


# ─── Jira ─────────────────────────────────────────────────────────────────


class JiraConnection(SQLModel, table=True):
    """A connected Jira site.

    Authenticated with an API token rather than OAuth 3LO, deliberately:
    Atlassian's OAuth needs a registered app with an HTTPS callback, which
    is the same wall Slack hit. An API token is created by any user from
    their own Atlassian account in under a minute and works on localhost —
    and it carries exactly that user's permissions, so it cannot see more
    of Jira than the person who created it can.
    """
    __tablename__ = "jira_connections"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    scope: ConnectionScope = ConnectionScope.WORKSPACE
    owner_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    # e.g. https://acme.atlassian.net
    site_url: str
    # The Atlassian account the token belongs to; also the Basic-auth user.
    account_email: str
    api_token_encrypted: str
    display_name: Optional[str] = None
    connected_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_synced_at: Optional[datetime] = None


class JiraProject(SQLModel, table=True):
    """A project chosen for indexing. Explicit, like Slack channels: a Jira
    site can hold dozens of projects that have nothing to do with support."""
    __tablename__ = "jira_projects"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    connection_id: str = Field(sa_column=Column(String, ForeignKey("jira_connections.id", ondelete="CASCADE"), nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    project_key: str
    name: str
    selected: bool = True
    issue_count: int = 0
    last_synced_at: Optional[datetime] = None


class JiraConnectionRead(SQLModel):
    id: str
    site_url: str
    account_email: str
    display_name: Optional[str]
    scope: ConnectionScope
    created_at: datetime
    last_synced_at: Optional[datetime]


# ─── Generic external connectors (Linear, Notion, Datadog, …) ─────────────
# Slack and Jira each got their own table because each has real structure
# worth modelling (channels with history; projects with issues). Everything
# after them shares one shape — credentials, a set of selectable resources,
# a sync cursor — so it gets one table and a per-provider adapter instead of
# a new table, router and migration per vendor.


class ConnectorProvider(str, Enum):
    LINEAR = "linear"
    NOTION = "notion"
    DATADOG = "datadog"
    # Not a vendor: documents the customer uploads directly (business flows,
    # runbooks, operations notes). Stored and searched through exactly the
    # same path as a connector, so it needs no separate retrieval code.
    CUSTOM_DOCS = "custom_docs"


class CustomDoc(SQLModel, table=True):
    """A document uploaded for context — the things that live in a Google
    Doc or someone's head rather than in a repo or a ticket."""
    __tablename__ = "custom_docs"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    title: str
    filename: Optional[str] = None
    size_bytes: int = 0
    chunk_count: int = 0
    uploaded_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CustomDocRead(SQLModel):
    id: str
    title: str
    filename: Optional[str]
    size_bytes: int
    chunk_count: int
    created_at: datetime


class ExternalConnection(SQLModel, table=True):
    __tablename__ = "external_connections"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    provider: ConnectorProvider
    scope: ConnectionScope = ConnectionScope.WORKSPACE
    owner_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    display_name: Optional[str] = None
    # Fernet-encrypted JSON: every provider needs a different set of secrets
    # (one key, two keys, a token plus a site), and a column per vendor
    # secret would be a migration per vendor.
    credentials_encrypted: str = ""
    # Non-secret settings (Datadog site, Notion page filters). Plain JSON so
    # it is inspectable without decrypting anything.
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    connected_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_synced_at: Optional[datetime] = None


class ConnectorResource(SQLModel, table=True):
    """A selectable unit inside a connection — a Linear team, a Notion
    database, a Datadog monitor tag. Selection is explicit for every
    provider, same as Slack channels and Jira projects."""
    __tablename__ = "connector_resources"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    connection_id: str = Field(sa_column=Column(String, ForeignKey("external_connections.id", ondelete="CASCADE"), nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    resource_id: str
    name: str
    selected: bool = True
    item_count: int = 0
    last_synced_at: Optional[datetime] = None


class ExternalConnectionRead(SQLModel):
    id: str
    provider: ConnectorProvider
    scope: ConnectionScope
    display_name: Optional[str]
    created_at: datetime
    last_synced_at: Optional[datetime]


# ─── Meetings ─────────────────────────────────────────────────────────────


class Meeting(SQLModel, table=True):
    """One call. `slug` is the human-shareable identifier (abcd-efgh) and
    doubles as the LiveKit room name, so a link, a room and a transcript are
    the same thing rather than three ids to reconcile."""
    __tablename__ = "meetings"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    slug: str = Field(sa_column=Column(String, unique=True, nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    title: Optional[str] = None
    created_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None

    # ── Call configuration, chosen before joining ──────────────────────
    # What the agent is FOR on this call. Multiple allowed: a call can be
    # both technical and onboarding, and the personas compose.
    bot_types: list = Field(default_factory=lambda: ["support"], sa_column=Column(JSON))
    # "english" or "multilingual" — this picks the voice stack (Deepgram vs
    # Sarvam), which is a per-call decision, not a per-deployment one.
    language_mode: str = "english"
    # Source group keys the agent may draw on for this call. Empty list is
    # meaningful (agent has nothing) and is NOT the same as null (use the
    # workspace defaults), which is why it is nullable.
    enabled_sources: Optional[list] = Field(default=None, sa_column=Column(JSON))
    # "speak": the agent is in the call and answers out loud. "whisper": it
    # transcribes the whole room and says nothing — suggestions go privately
    # to each member's thread. Fixed at creation: the worker picks its
    # listening machinery when it joins.
    mode: str = "speak"
    # Who the agent is attending FOR. "member": it is that person's agent —
    # labelled "<name>'s Photon", speaking from their context, with them as
    # the point of contact it escalates to. "company": it represents the
    # workspace, which only an owner can switch on (Workspace.org_agent_enabled).
    attends_as: str = "member"
    represents_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


class KnockStatus(str, Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    DENIED = "denied"


class MeetingKnock(SQLModel, table=True):
    """Someone asking to be let into a call.

    A meeting code is shareable — that is the point — but a shared link
    forwarded one hop too far should not put a stranger into a live customer
    call. So the code gets you to the door; someone already inside opens it.

    Workspace members skip this: they are already trusted with the
    workspace's data, and making colleagues queue to join their own team's
    call is friction that teaches people to admit anyone without looking.
    """
    __tablename__ = "meeting_knocks"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    meeting_id: str = Field(sa_column=Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True))
    display_name: str
    user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    status: KnockStatus = KnockStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


class KnockRead(SQLModel):
    id: str
    display_name: str
    status: KnockStatus
    created_at: datetime
    is_member: bool = False


class TranscriptRole(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


class TranscriptEntry(SQLModel, table=True):
    """One line of the shared transcript.

    Rows rather than appending to a single markdown column: several
    participants and the agent all write during a call, and concurrent
    read-modify-write on one text field silently loses lines. The markdown
    is rendered from these on demand (GET .../transcript.md), so the
    deliverable is still one common .md.
    """
    __tablename__ = "transcript_entries"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    meeting_id: str = Field(sa_column=Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True))
    role: TranscriptRole = TranscriptRole.HUMAN
    # Display name as seen on the call ("rishik@…", "Client A", "Photon").
    speaker_name: str
    # Participant identity ("user:<uuid>" / "guest:<rand>"); null for the agent.
    speaker_identity: Optional[str] = None
    # Set only when the speaker is a signed-in user, so a transcript can be
    # tied back to a person without trusting a display name.
    speaker_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    text: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MeetingRead(SQLModel):
    id: str
    slug: str
    title: Optional[str]
    workspace_id: str
    bot_types: list = []
    language_mode: str = "english"
    enabled_sources: Optional[list] = None
    mode: str = "speak"
    attends_as: str = "member"
    represents_user_id: Optional[str] = None
    created_at: datetime
    ended_at: Optional[datetime]


class TranscriptEntryCreate(SQLModel):
    role: TranscriptRole = TranscriptRole.HUMAN
    speaker_name: str
    speaker_identity: Optional[str] = None
    text: str


class WhisperStatus(str, Enum):
    LIVE = "live"
    ENDED = "ended"


class WhisperSource(str, Enum):
    """Where a session's transcript comes from.

    PHOTON needs nothing external — Photon's own call already transcribes
    every turn, so whisper works on it today. The other two exist because
    whisper's whole point is working on the client's platform, which we do
    not control: EXTERNAL is any client POSTing lines (a meeting-bot
    vendor's webhook, a browser extension, a desktop capture), and BOT is a
    session a vendor is driving on our behalf.
    """
    PHOTON = "photon"
    EXTERNAL = "external"
    BOT = "bot"


class WhisperSession(SQLModel, table=True):
    """One meeting being whispered on.

    Deliberately NOT the same row as Meeting. A Meeting is Photon's own call
    (it owns a LiveKit room, a slug, a waiting room); a whisper session is
    usually somebody else's meeting on somebody else's platform, where we
    have a transcript and nothing else. Forcing them into one table would
    mean every whisper session inventing a room it does not have.
    """
    __tablename__ = "whisper_sessions"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    title: Optional[str] = None
    source: WhisperSource = WhisperSource.EXTERNAL
    status: WhisperStatus = WhisperStatus.LIVE
    # Set when whispering on one of Photon's own calls, so the two records
    # can be joined without duplicating the transcript.
    meeting_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True))
    # The meeting URL, or a bot vendor's id for the session. Free text
    # because every platform names its meetings differently.
    external_ref: Optional[str] = None
    created_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    # Authenticates the vendor's transcript webhook, which cannot carry a user
    # session. Per-session rather than per-deployment so a leaked URL exposes
    # ONE meeting's transcript and dies when that session ends, instead of
    # being a standing key to every future call.
    webhook_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(24))
    # The meeting-bot vendor's last reported state (joining_call,
    # in_waiting_room, in_call_recording, call_ended, fatal…). Shown to the
    # rep, because "waiting to be admitted" needs THEM to click Admit.
    bot_status: Optional[str] = None
    bot_status_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None


class WhisperLine(SQLModel, table=True):
    """One line of what was actually said IN the meeting — shared, not private.

    `is_client` is the whole gate: a suggestion fires on what the CLIENT
    asks, not on what our own people say, or the agent would answer its own
    side of the conversation. It is set by the ingesting caller, which knows
    who is external; when that is unknown the safe default is False, since a
    missed suggestion is recoverable and an unprompted answer to your own
    colleague is noise in front of a customer.
    """
    __tablename__ = "whisper_lines"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(sa_column=Column(String, ForeignKey("whisper_sessions.id", ondelete="CASCADE"), nullable=False, index=True))
    speaker_name: str
    text: str
    is_client: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WhisperThread(SQLModel, table=True):
    """One team member's private thread on one session.

    Private is enforced by the row, not by the UI: a thread belongs to a
    user_id, and every read checks it. The client is not a workspace member
    and has no thread, so there is nothing for them to be shown even if they
    somehow reached the API.
    """
    __tablename__ = "whisper_threads"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(sa_column=Column(String, ForeignKey("whisper_sessions.id", ondelete="CASCADE"), nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WhisperRole(str, Enum):
    SUGGESTION = "suggestion"  # unprompted, triggered by a client question
    MEMBER = "member"          # the team member typing privately
    AGENT = "agent"            # Photon answering the member directly


class WhisperMessage(SQLModel, table=True):
    """A line in a private thread. Persists after the call by construction —
    it is a row, and nothing deletes it when the session ends."""
    __tablename__ = "whisper_messages"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    thread_id: str = Field(sa_column=Column(String, ForeignKey("whisper_threads.id", ondelete="CASCADE"), nullable=False, index=True))
    role: WhisperRole = WhisperRole.SUGGESTION
    text: str
    # What the client said that triggered an unprompted suggestion, so the
    # member can see what Photon thought it was answering.
    trigger_text: Optional[str] = None
    # The Section 4 answer contract's own fields, kept so a suggestion can be
    # rendered with the same citation chips as any other answer.
    evidence: Optional[list] = Field(default=None, sa_column=Column(JSON))
    warnings: Optional[list] = Field(default=None, sa_column=Column(JSON))
    confidence: Optional[str] = None
    abstained: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WhisperSessionRead(SQLModel):
    # Deliberately without webhook_secret: this shape is returned by list
    # endpoints, and a credential that rides along with every listing ends up
    # in logs, screenshots and browser history. It has its own endpoint.
    id: str
    title: Optional[str]
    source: WhisperSource
    status: WhisperStatus
    external_ref: Optional[str]
    meeting_id: Optional[str]
    created_at: datetime
    ended_at: Optional[datetime]
    bot_status: Optional[str] = None
    line_count: int = 0


class WhisperLineCreate(SQLModel):
    speaker_name: str
    text: str
    # None means "work it out from the speaker name" (whisper/participants.py).
    # An explicit value always wins — Photon's own calls know authoritatively.
    is_client: Optional[bool] = None
    # When the platform reports it; a stronger member match than a name.
    speaker_email: Optional[str] = None


class WhisperMessageRead(SQLModel):
    id: str
    role: WhisperRole
    text: str
    trigger_text: Optional[str]
    evidence: Optional[list]
    warnings: Optional[list]
    confidence: Optional[str]
    abstained: bool
    created_at: datetime


class GitHubInstallation(SQLModel, table=True):
    __tablename__ = "github_installations"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    scope: ConnectionScope = ConnectionScope.WORKSPACE
    # Set only when scope is USER. Kept nullable rather than defaulting to
    # the connector so a workspace-scoped source has no misleading "owner".
    owner_user_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True))
    installation_id: int = Field(sa_column=Column(Integer, unique=True, nullable=False, index=True))
    account_login: str
    account_type: str  # "User" or "Organization", as GitHub reports it
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GitHubInstallationRead(SQLModel):
    id: str
    installation_id: int
    account_login: str
    account_type: str
    created_at: datetime


# ─── Enums ────────────────────────────────────────────────────────────────────

class RepoStatus(str, Enum):
    PENDING = "PENDING"
    INGESTING = "INGESTING"
    READY = "READY"
    FAILED = "FAILED"


class RepoSourceType(str, Enum):
    GITHUB = "github"
    ZIP = "zip"
    LOCAL = "local"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class QueryIntent(str, Enum):
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    RELATIONAL = "relational"
    CROSS_CUTTING = "cross_cutting"


# ─── Repo ─────────────────────────────────────────────────────────────────────

class RepoBase(SQLModel):
    name: str
    source_type: RepoSourceType
    source_url: Optional[str] = None
    status: RepoStatus = RepoStatus.PENDING
    local_path: Optional[str] = None
    # Summary card populated after ingestion
    file_count: int = 0
    function_count: int = 0
    language_breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # FIX: Replaced List[str] with list[str]
    top_modules: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    cluster_count: int = 0
    # FIX: Replaced List[str] with list[str]
    most_imported: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    error_message: Optional[str] = None
    # How long ingestion actually took, in seconds. Recorded so the
    # "this will take about N minutes" estimate shown before importing
    # repos is calibrated from THIS deployment's real measurements rather
    # than a number someone guessed once (see app/services/estimate.py).
    ingest_seconds: Optional[float] = None
    # Fictional "Adventa" content from app/mock/, not a real connection —
    # see routers/mock.py. Kept as its own flag rather than inferred from
    # source_url so the dashboard/evidence panel can label it without
    # string-matching a path.
    is_mock: bool = False


class Repo(RepoBase, table=True):
    __tablename__ = "repos"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    # owner_id stays as provenance ("who connected this"); workspace_id is
    # what authorisation is actually checked against.
    owner_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    workspace_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True))
    # Set only for repos imported via a GitHub App installation (the repo
    # picker, routers/github_app.py) — used both to diff "already imported"
    # vs "new" repos when the picker re-opens, and to know at clone time
    # (tasks/ingestion.py) that a minted installation token should be used
    # instead of the single static github_token.
    github_repo_id: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True, index=True))
    github_installation_id: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True, index=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    jobs: List["Job"] = Relationship(back_populates="repo", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    pins: List["Pin"] = Relationship(back_populates="repo", sa_relationship_kwargs={"cascade": "all, delete-orphan"})


class RepoCreate(SQLModel):
    name: str
    source_type: RepoSourceType
    source_url: Optional[str] = None


class RepoRead(RepoBase):
    id: str
    owner_id: Optional[str]
    created_at: datetime
    updated_at: datetime


# ─── Job ──────────────────────────────────────────────────────────────────────

class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    repo_id: str = Field(sa_column=Column(String, ForeignKey("repos.id", ondelete="CASCADE"), nullable=False))
    repo: Optional[Repo] = Relationship(back_populates="jobs")
    status: JobStatus = JobStatus.QUEUED
    celery_task_id: Optional[str] = None
    progress: int = 0          # 0-100
    phase: str = "queued"      # queued | cloning | parsing | graphing | embedding | done
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


class JobRead(SQLModel):
    id: str
    repo_id: str
    status: JobStatus
    progress: int
    phase: str
    message: str
    created_at: datetime
    finished_at: Optional[datetime]


# ─── Pin (annotation) ─────────────────────────────────────────────────────────

class Pin(SQLModel, table=True):
    __tablename__ = "pins"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    repo_id: str = Field(sa_column=Column(String, ForeignKey("repos.id", ondelete="CASCADE"), nullable=False))
    repo: Optional[Repo] = Relationship(back_populates="pins")
    module_node_id: str          # Neo4j node id this pin is attached to
    question: str
    answer: str
    # FIX: Replaced List[dict] with list[dict]
    cited_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    is_stale: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PinCreate(SQLModel):
    repo_id: str
    module_node_id: str
    question: str
    answer: str
    # FIX: Replaced List[dict] with list[dict]
    cited_refs: list[dict] = []


class PinRead(SQLModel):
    id: str
    repo_id: str
    module_node_id: str
    question: str
    answer: str
    # FIX: Replaced List[dict] with list[dict]
    cited_refs: list[dict]
    is_stale: bool
    created_at: datetime


# ─── Query ────────────────────────────────────────────────────────────────────

class QueryRequest(SQLModel):
    repo_id: str
    question: str
    session_id: Optional[str] = None
    file_context_path: Optional[str] = None  # Scope AI answer to a specific file


class QueryResponse(SQLModel):
    session_id: str
    intent: QueryIntent
    answer: str
    # FIX: Replaced List[dict] with list[dict]
    cited_chunks: list[dict] = []
    # FIX: Replaced List[dict] with list[dict]
    graph_nodes: list[dict] = []


# ─── Learning Path Cache ───────────────────────────────────────────────────────

class LearningPathCache(SQLModel, table=True):
    __tablename__ = "learning_path_cache"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    repo_id: str = Field(sa_column=Column(String, ForeignKey("repos.id", ondelete="CASCADE"), nullable=False, index=True, unique=True))
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    generated_at: datetime = Field(default_factory=datetime.utcnow)

# ─── Agent jobs — a member's agent fixing a ticket through the harness ─────────
# One row per ticket an agent takes on. The member it acts for (`owner_user_id`)
# is its point of contact: they approve or narrow the plan, and anything the
# agent cannot finish comes back to them. The harness does the work; this row
# is Photon's record of what was asked, what was approved and what came of it.

class AgentJobStatus(str, Enum):
    DRAFT = "draft"                          # captured (e.g. from a call); not yet confirmed
    PLANNING = "planning"                    # harness plan run in flight
    AWAITING_APPROVAL = "awaiting_approval"  # plan posted, waiting on the owner
    FIXING = "fixing"                        # harness fix run in flight
    PR_OPEN = "pr_open"                      # verified, in scope, PR opened
    ESCALATED = "escalated"                  # the agent stopped; the owner decides
    REJECTED = "rejected"                    # the owner said no
    FAILED = "failed"                        # the machinery broke, not the fix


class AgentJobSource(str, Enum):
    MANUAL = "manual"
    GITHUB = "github"
    LINEAR = "linear"
    JIRA = "jira"
    MEETING = "meeting"


class AgentJob(SQLModel, table=True):
    __tablename__ = "agent_jobs"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    owner_user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    # Null only for a DRAFT: a commitment heard on a call does not say which
    # repository it is about; the member picks one when confirming.
    repo_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("repos.id", ondelete="CASCADE"), nullable=True))
    meeting_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True))
    # Strings rather than PG enums: create_all never alters an enum type, and
    # these lists will grow (see the workspacerole note in database.py).
    status: str = Field(default=AgentJobStatus.PLANNING.value, sa_column=Column(String, nullable=False, index=True))
    source: str = Field(default=AgentJobSource.MANUAL.value, sa_column=Column(String, nullable=False))
    # Tracker reference: "owner/repo#12" for GitHub, "ENG-42" for Linear.
    ticket_ref: Optional[str] = None
    ticket_url: Optional[str] = None
    # The tracker's own id (Linear's issue uuid) and the connection whose
    # key reads and comments on it — the member's own, at their access.
    external_id: Optional[str] = None
    connection_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("external_connections.id", ondelete="SET NULL"), nullable=True))
    title: str
    issue_text: str
    # "member": the owner's own agent. "company": the org agent took an
    # unowned ticket, and the owner here is the workspace owner approving
    # on the company's behalf.
    acting_for: str = "member"
    plan_run_id: Optional[str] = None
    fix_run_id: Optional[str] = None
    # What the harness proposed: root cause + change plan, as it wrote them.
    plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # What the owner approved: {"files": [...], "must_not": [...], "constraints": str}.
    approved_scope: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    approved_by: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    # The fix run's outcome, verification, confidence, scope check and diff.
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    pr_url: Optional[str] = None
    escalation_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentJobRead(SQLModel):
    id: str
    workspace_id: str
    owner_user_id: str
    repo_id: Optional[str]
    meeting_id: Optional[str] = None
    status: str
    source: str
    ticket_ref: Optional[str]
    ticket_url: Optional[str]
    title: str
    issue_text: str
    acting_for: str = "member"
    plan: Optional[dict]
    approved_scope: Optional[dict]
    result: Optional[dict]
    pr_url: Optional[str]
    escalation_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


# ─── Agent profile — a member's own instructions to their agent ────────────
# Edited by the member, never by anyone else. It shapes how their agent
# speaks for them and what it must hand back rather than answer.

class AgentProfile(SQLModel, table=True):
    __tablename__ = "agent_profiles"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    # How they want to be named out loud ("Priya"); falls back to the email.
    display_name: Optional[str] = None
    # Topics the agent must escalate rather than answer, even when it could
    # ("pricing", "roadmap", "security").
    always_escalate: list = Field(default_factory=list, sa_column=Column(JSON))
    # What the agent may commit to on their behalf, in their words.
    may_commit_to: Optional[str] = None
    notes: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Escalations — the agent asking its person, live, mid-meeting ──────────

class EscalationStatus(str, Enum):
    OPEN = "open"                    # waiting on the member, clock running
    ANSWERED = "answered"            # answered in time; the agent relays it
    DECLINED = "declined"            # the member said "skip it"
    EXPIRED = "expired"              # no reply in time; the agent apologised
    ANSWERED_LATE = "answered_late"  # replied after the agent moved on: a follow-up


class Escalation(SQLModel, table=True):
    __tablename__ = "escalations"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True))
    meeting_id: Optional[str] = Field(default=None, sa_column=Column(String, ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True))
    meeting_title: Optional[str] = None
    poc_user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    status: str = Field(default=EscalationStatus.OPEN.value, sa_column=Column(String, nullable=False, index=True))
    question: str
    topic: str = "technical"
    importance: str = "medium"
    reason: str = ""
    # The last few lines of the meeting, so the member sees what led here.
    context: list = Field(default_factory=list, sa_column=Column(JSON))
    # What the agent had, even if it would not say it: often enough for the
    # member to answer in one word.
    draft_answer: Optional[str] = None
    holding_line: str = ""
    answer: Optional[str] = None
    notified_via: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    resolved_at: Optional[datetime] = None


# ─── Chrome extension pairing — whisper inside Google Meet ─────────────────
# The extension never sees a password or a full login token. The member makes
# a one-time code in Photon, types it into the extension, and the extension
# swaps it for a token that can only reach whisper routes in that workspace.

class ExtensionPairing(SQLModel, table=True):
    __tablename__ = "extension_pairings"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    # sha256 of the code: a database read must not hand out live codes.
    code_hash: str = Field(sa_column=Column(String, unique=True, nullable=False, index=True))
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False))
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ExtensionDevice(SQLModel, table=True):
    """One paired browser. Its id is the token's jti, so revoking the row
    kills the token on its next request."""
    __tablename__ = "extension_devices"
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(sa_column=Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True))
    workspace_id: str = Field(sa_column=Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False))
    name: str = "Chrome"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
