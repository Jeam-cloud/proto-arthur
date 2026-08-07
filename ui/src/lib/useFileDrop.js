// Drop handlers, shared by every element that should accept a dropped file.
//
// WHY a hook rather than handlers on the composer: the drop target used to be
// the composer alone, so dragging a file over the CONVERSATION -- which is most
// of the window, and the obvious place to aim -- showed the "no drop" cursor
// and did nothing. The whole chat should accept files; only the highlight
// belongs on the composer, because that is where they land.
//
// WHY the depth counter is MODULE-LEVEL rather than per-hook: dragenter and
// dragleave fire for every element the pointer crosses, so each mounted
// instance keeping its own count would see enters and leaves that belong to a
// sibling and flicker. One counter across all drop zones means moving from the
// message list onto the composer is what it looks like -- a continuous drag --
// instead of a leave followed by an enter.
import { useCallback } from "react";
import { useAttachments } from "../stores/attachments";

let depth = 0;

export function useFileDrop() {
  const addFiles = useAttachments((s) => s.addFiles);
  const setDragging = useAttachments((s) => s.setDragging);

  // Only react to FILES. A drag carrying text (selected words, a link, a
  // conversation being reordered in the sidebar) must pass straight through,
  // or every internal drag would light up the composer.
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

  const onDragEnter = useCallback((e) => {
    if (!hasFiles(e)) return;
    depth += 1;
    setDragging(true);
  }, [setDragging]);

  const onDragOver = useCallback((e) => {
    // preventDefault is what actually makes an element a drop target. Without
    // it the browser runs its default handler and shows the "no drop" cursor,
    // which is exactly what the message area was doing.
    if (hasFiles(e)) e.preventDefault();
  }, []);

  const onDragLeave = useCallback((e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) setDragging(false);
  }, [setDragging]);

  const onDrop = useCallback((e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    setDragging(false);
    const files = e.dataTransfer?.files;
    if (files?.length) addFiles(files);
  }, [addFiles, setDragging]);

  return { onDragEnter, onDragOver, onDragLeave, onDrop };
}
