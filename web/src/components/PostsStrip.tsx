import { usePosts, useUploadStatus } from "../api/client";
import type { Bin, PostOut, PostStatus } from "../api/types";
import { mediaLabel } from "./mediaLabel";

const STATUS_WORDS: Record<PostStatus, string> = {
  draft: "draft, not sent",
  uploading: "uploading…",
  uploaded: "uploaded",
  failed: "upload failed",
};

interface Props {
  slug: string;
  bin: Bin;
}

/**
 * The posts drafted from this bin: a video with its title and description, written by the agent
 * and held private until the user says to upload. Read-only here (the agent writes them through
 * `publish`); shown only when there is one, so a bin with no post looks as it did. The real
 * form -- a Publish button, a post editor -- is the design pass's, not this strip's.
 */
export function PostsStrip({ slug, bin }: Props) {
  const posts = usePosts(slug, bin.id);
  const list = posts.data ?? [];
  if (list.length === 0) return null;
  return (
    <section className="posts-strip" aria-label="Posts">
      <div className="posts-strip-head">
        <span className="meta">
          {list.length === 1 ? "Post" : `${list.length} posts`}
        </span>
      </div>
      {list.map((p) => (
        <PostRow key={p.id} slug={slug} post={p} bin={bin} />
      ))}
    </section>
  );
}

function PostRow({ slug, post, bin }: { slug: string; post: PostOut; bin: Bin }) {
  const video = post.media_record;
  const about = video ? mediaLabel(bin, video) : "video deleted";
  const upload = useUploadStatus(slug, post.id, post.status === "uploading");
  const running = post.status === "uploading" && upload.data?.status === "running" ? upload.data : null;
  const status = running ? `uploading… ${Math.round((running.progress ?? 0) * 100)}%` : STATUS_WORDS[post.status];
  return (
    <div className={`post-row is-${post.status}`}>
      <span className="post-row-text">
        <b className="post-row-title">{post.title || "(no title yet)"}</b>
        <span className="meta">
          {post.platform}
          {post.channel_handle ? ` ${post.channel_handle}` : post.channel ? " (channel not connected here)" : ""} · {status} · {post.privacy} · {about}
          {post.publish_at ? ` · scheduled ${post.publish_at}` : ""}
        </span>
        {post.description && <span className="post-row-desc">{post.description}</span>}
        {post.error && <span className="post-row-error">{post.error}</span>}
      </span>
      {post.studio_url && (
        <a className="btn-outline post-row-link" href={post.studio_url} target="_blank" rel="noreferrer" title="finish it in YouTube Studio: thumbnail, end screens, publish">
          Studio ↗
        </a>
      )}
    </div>
  );
}
