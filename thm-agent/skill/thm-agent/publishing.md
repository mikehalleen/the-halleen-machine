# Publishing: the post

A **post** is a finished video with the words that go with it, drafted for a platform
(YouTube today) and held in THM until the user sends it. You write the words; the user
publishes. Read this before the first `publish new`.

## What a post is

- Made from a bin's **current video**: a sequence's output bin after `sequence export`, or
  any bin whose current media is a video. `publish new --bin <id>` takes the current one;
  `--media <id>` names another video in that bin. The video is frozen on the post: a later
  take does not swap in (`publish set <post> --media <id>` moves it on purpose).
- Fields: `title`, `description`, `tags`, `privacy` (private by default), the altered-or-
  synthetic disclosure (`--synthetic`, **on** by default: THM output is made), made-for-kids
  (off), and later a `--thumbnail` (an image media) and `--publish-at` (a schedule).
- Status `draft` until the upload. `publish upload <post>` sends the video to the **project's
  channel**, **private**, and puts a YouTube Studio link in the feed; the thumbnail, end
  screens, cards and the Publish button are theirs, in Studio (none of those has an API). Once
  uploaded, the post is closed to edits here.

## The channel

A Google account owns several channels (a personal one and brand accounts, each with its own
handle), and THM uploads to the one the **project** names: the user picks it in the project
settings (Project settings > YouTube) from the channels connected on the server, and connects a
new one there too, at the PC. A post copies the project's channel when it is drafted and goes
there. None of this is yours:

- `publish check` says which channels the server knows and which one this project posts to.
- A project with no channel refuses `publish new` with `no_channel`; a project whose channel's
  token is gone refuses `unknown_channel`. Either way, `post` once -- "this project has no
  YouTube channel yet; pick one in Project settings > YouTube" -- and move on. Do not retry, do
  not pick a channel, and never try `set_project_settings youtube_channel` (refused
  `wrong_actor`).
- When the user names a handle in the brief or a note, check it against `publish check`; if it is
  not this project's, say so in a `post` and wait. The card and the feed name the channel
  (`upload "..." to youtube @halleen (private)`), so the user sees where it goes before pressing
  Upload.

## The upload

- **The words first, then one card.** While the user is still shaping the title or the
  description (a note that changes a line, "shorter", "add the models"), you are editing a
  draft: `publish set`, then `post` the full text so they can read it, and wait. Nothing is
  sent, so there is no hurry and no card. The upload card goes out once, when the user says
  the text is done or asks for the upload. Never send the card to show them the text; the
  card shows the title only, and `post` is where the text is read.
- **The card is the user's call.** `publish upload <post> --reason "..."` sends "Upload this
  video to YouTube as private?"; the transfer starts only when they press Upload. **No**,
  closing the card, or a reply does nothing: the post stays a draft. A reply on the card is a
  change to the text: `publish set`, `post` the new text, and when they say go, a new card
  (the replied one is finished; the server refuses a second card while the first still waits,
  `card_exists`, and the waiting card uploads the post as it stands at approval, so an edit
  before the click flows into it). When the user asked for the upload in the feed,
  `--requested` lands and starts it at once, and `--wait` follows it to the links. A bare
  upload is refused (`propose_required`).
- **The channel is the user's to manage.** An uploaded post is closed to edits here (YouTube
  has the words now); a changed text after an upload is a **new post** with a new card, when
  the user asks for one. Do not push back on a second upload of the same video, cite the
  earlier one, or count the quota at them: they delete and arrange videos in Studio, and they
  asked. Say what the new post is and send the card.
- **Before the card**: the title is written (`title_required` otherwise), the description and
  tags are on the record, and `publish check` says the server is configured and the project has
  a channel. A server that is not refuses with `youtube_unconfigured` and the one-line fix: that
  fix is the user's to do at the PC (an environment variable and a consent in a browser), so
  `post` it once and move on; do not retry.
- **A failed upload** (`publish status` says why: quota, a dead token, a refused file) is the
  same post uploaded again once the cause is fixed.
- **After**: answer the note with the Studio link's existence, not the link itself ("uploaded,
  private; the Studio link is in the feed"), and `seen` the export's take. Never say
  "published".
- Every write is a logged change the user sees in the feed with your `--reason`, like any
  other edit. Describe the post by its title in a post or an answer, never by its id.

## When

Draft a post when the user asks for one, or when a sequence you were asked to deliver has
been exported and the user's QC passed it and the brief says it is for a channel. One post
per deliverable; revise the same post rather than drafting a second for the same video. A
post for a video that has not passed the user's QC is premature: deliver the export first.

## YouTube's limits (the server refuses past them, `bad_value`)

| field | limit |
|---|---|
| title | 1 to 100 characters, no `<` or `>` |
| description | up to 5000 bytes (UTF-8), no `<` or `>` |
| tags | up to 500 characters together (commas count; a tag with a space counts two more) |
| privacy | `private` or `unlisted` from you; **never public** -- the user does that |
| publish_at | ISO 8601, and the post must be private until then |

## Writing the title

- One idea, said the way the viewer would search for it or say it to a friend. Under 60
  characters reads whole on a phone; the rest is cut with an ellipsis.
- Name the thing and the payoff: what it is and why to watch ("THM cuts a 12-shot
  sequence from one brief"). No clickbait, no ALL CAPS, no emoji.
- Series or product first only when the channel is built on it; otherwise the hook first.
- Offer the user two or three variants in a `post` when the brief does not fix the title;
  put the best on the draft.

## Writing the description

- The first two lines show before "more": say what the video is and who it is for, in
  plain words, then a line break.
- Then what happens in it, as a short paragraph or chapters (`0:00 intro`, one per line,
  starting at `0:00`, in order; YouTube turns them into chapters by itself).
- Then the credits and the facts: made with THM and ComfyUI, the models or workflows by
  name when the user wants them public, music credit when a track is in it.
- Links last, one per line, with a word before each saying what it is. No link in the
  first two lines. The server refuses `<` and `>`, so no HTML.
- Hashtags: up to three, at the very end, only ones the channel uses.
- Write it to a file and send `--description-file`: line breaks survive, shell quoting does
  not.

## Tags

Five to fifteen, lower case, the words a viewer would type: the tool, the subject, the
format. No tag that is not true of the video; no competitor names.

## What is the user's

The thumbnail picture (you may set one with `--thumbnail` once the upload supports it),
end screens, cards, the Publish button, and anything that says who the channel is. Never
set `privacy` to anything but `private` or `unlisted`; never promise the user a publish
time; never say "published" -- the word for what the upload does is **uploaded, private**.
