// API shapes. Aliases over src/api/openapi.d.ts, which `npm run types`
// regenerates from the running server's OpenAPI document. The exported names
// are stable; screens import from here, never from openapi.d.ts directly.

import type { components } from "./openapi";

type Schemas = components["schemas"];

export type Media = Schemas["Media"];
export type MediaOrigin = Schemas["MediaOrigin"];
export type QcMark = Schemas["QcMark"];
export type Lora = Schemas["Lora"];
export type LoraFile = Schemas["LoraFile"];
export type Reference = Schemas["Reference"];
export type Bin = Schemas["Bin"];
export type BinCard = Schemas["BinCard"];
export type BinDetail = Schemas["BinDetail"];
export type BinRef = Schemas["BinRef"];
export type Recipe = Schemas["RecipeOut"];
export type AgentSession = Schemas["AgentSession"];
export type ThumbRef = Schemas["ThumbRef"];
export type ProjectCard = Schemas["ProjectCard"];
export type ProjectDetail = Schemas["ProjectDetail"];
export type CapacityOut = Schemas["CapacityOut"];
export type WorkflowInfo = Schemas["WorkflowInfo"];
export type ReferenceOut = Schemas["ReferenceOut"];
export type MediaPage = Schemas["MediaPage"];
export type HealthOut = Schemas["HealthOut"];
export type Change = Schemas["Change"];
export type Decision = Schemas["Decision"];
export type Message = Schemas["Message"];
export type MessageContext = Schemas["MessageContext"];
export type Generation = Schemas["Generation"];
export type InboxOut = Schemas["InboxOut"];
export type CommandResultOut = Schemas["CommandResultOut"];
export type JobOut = Schemas["JobOut"];
export type JobStatus = JobOut["status"];
export type FeedEntry = Schemas["FeedEntry"];
export type FeedOut = Schemas["FeedOut"];
export type ShotOut = Schemas["ShotOut"];
export type ShotRefOut = Schemas["ShotRefOut"];
export type ShotDecision = Schemas["ShotDecision"];
export type ClosePendingOut = Schemas["ClosePendingOut"];
export type UploadOut = Schemas["UploadOut"];
export type SeenOut = Schemas["SeenOut"];
export type ProjectSettingsBody = Schemas["ProjectSettingsBody"];
export type ProjectDefaults = Schemas["ProjectDefaults"];
export type RemoveUploadOut = Schemas["RemoveUploadOut"];
export type ClipBody = Schemas["ClipBody"];
export type EnhanceBody = Schemas["EnhanceBody"];
export type EnhanceOut = Schemas["EnhanceOut"];
export type EnhanceStatusOut = Schemas["EnhanceStatusOut"];
export type AgentActivityOut = Schemas["AgentActivityOut"];
export type AgentStatusOut = Schemas["AgentStatusOut"];
export type EnhanceEngine = EnhanceBody["engine"];
export type ClipMode = ClipBody["mode"];
export type DecisionStatus = Decision["status"];
export type MessageKind = Message["kind"];
export type SessionStatus = AgentSession["status"];
/** How much the agent asks: auto loops on the goal, the manual modes ask per render (videos, or every render), plan revises the brief. */
export type SessionMode = AgentSession["mode"];

// Narrow unions lifted from the generated fields so screens can name them.
export type MediaType = Media["type"];
/** What a workflow makes, and so what a generation is. A bin has no kind of its own. */
export type Kind = WorkflowInfo["kind"];
export type Actor = QcMark["by"];
export type SubLabel = NonNullable<ReferenceOut["sub_label"]>;

// Error bodies come from the server's exception handler (thm_server/errors.py),
// not from a response model, so they have no generated schema.
export interface ApiErrorBody {
  code: string;
  detail: string;
  extra: Record<string, unknown>;
}
