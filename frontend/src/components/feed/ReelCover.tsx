import { useEffect, useState } from "react";
import { FeedClip } from "../../types/echoflow";
import { WaveStage } from "./WaveStage";

interface ReelCoverProps {
  clip: FeedClip;
  /** True only on the card the player considers current. */
  active: boolean;
}

/**
 * The reel's imagery: the creator's uploaded cover when there is one, the
 * sound wave when there is not.
 *
 * The cover URL is a presigned S3 link that expires after about an hour, and
 * feed pages are cached in `sessionStorage` for five minutes — so a page read
 * back from the cache can easily hold a URL that died in between. A cover that
 * fails to load MUST fall back to the wave, not to a broken-image glyph: the
 * `onError` swap is the whole reason this component exists rather than a bare
 * `<img>` in the card.
 *
 * `cover_image` is also `null` until a reviewer approves the upload, so most
 * clips legitimately have no cover at all. Either way the stage is never
 * empty: the wave is the backup option the brief asked for.
 *
 * The image is decorative (`alt=""`): the clip's title and creator are real
 * text beside it, and a description of the picture would add nothing a
 * listener needs.
 */
export function ReelCover({ clip, active }: ReelCoverProps) {
  const [coverFailed, setCoverFailed] = useState(false);

  // A different clip behind the same component instance (feed refresh, list
  // reorder) must not inherit the previous clip's failure: the new cover gets
  // its own chance to load.
  useEffect(() => {
    setCoverFailed(false);
  }, [clip.id, clip.cover_image]);

  if (!clip.cover_image || coverFailed) {
    return <WaveStage clip={clip} active={active} />;
  }

  return (
    <img
      src={clip.cover_image}
      alt=""
      loading="lazy"
      onError={() => setCoverFailed(true)}
      className="h-full w-full object-cover"
      // Not `draggable={false}` or friends: those are polish, and the card
      // below layers its own interactions over this stage.
    />
  );
}
