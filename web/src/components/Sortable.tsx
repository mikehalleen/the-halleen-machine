import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import {
  DndContext,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

/**
 * Drag-to-reorder on dnd-kit. A mouse drags after 6px of travel, so a plain click is still a
 * click; a finger drags after a 200ms hold, so a swipe still scrolls the page; arrow keys move
 * a focused handle. Items slide into place as the dragged one crosses them.
 *
 * `SortableList` owns the order between the drop and the server's answer: the dropped order
 * shows until `ids` changes (the refetch) or the commit fails, so nothing snaps back around the
 * round trip. `SortableItem` hands its child the refs and props to spread: `item` on the moving
 * element and `handle` on whatever you drag it by — the whole item, or a grip inside it. A row
 * that is itself a link takes `pointerHandle` on the row (mouse and finger drag anywhere on it)
 * and keeps `handle` on a grip for the keyboard, so Enter on the link still follows it.
 */

interface Ctx {
  swallowClick: () => boolean;
}

const SortableCtx = createContext<Ctx>({ swallowClick: () => false });

interface ListProps {
  ids: string[];
  onCommit: (order: string[]) => Promise<unknown>;
  /** `vertical` for a column of rows; `grid` for a wrapping row of tiles. */
  layout?: "vertical" | "grid";
  children: (order: string[]) => ReactNode;
}

export function SortableList({ ids, onCommit, layout = "vertical", children }: ListProps) {
  const [override, setOverride] = useState<string[] | null>(null);
  const justDragged = useRef(false);
  const key = ids.join("|");
  useEffect(() => {
    setOverride(null);
  }, [key]);
  const order = override ?? ids;

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (e: DragEndEvent) => {
    // the click a mouse fires on release would follow the row's link; let it pass on a tap only
    justDragged.current = true;
    setTimeout(() => (justDragged.current = false), 0);
    const over = e.over?.id;
    if (over == null || over === e.active.id) return;
    const from = order.indexOf(String(e.active.id));
    const to = order.indexOf(String(over));
    if (from < 0 || to < 0) return;
    const next = arrayMove(order, from, to);
    setOverride(next);
    onCommit(next).catch(() => setOverride(null));
  };

  return (
    <SortableCtx.Provider value={{ swallowClick: () => justDragged.current }}>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={order} strategy={layout === "grid" ? rectSortingStrategy : verticalListSortingStrategy}>
          {children(order)}
        </SortableContext>
      </DndContext>
    </SortableCtx.Provider>
  );
}

export interface SortableRender {
  isDragging: boolean;
  /** Spread on the element that moves. */
  item: {
    ref: (el: HTMLElement | null) => void;
    style: CSSProperties;
    onClickCapture: (e: ReactMouseEvent<HTMLElement>) => void;
    onDragStart: (e: ReactMouseEvent<HTMLElement>) => void;
  };
  /** Spread on the element you drag by. */
  handle: Record<string, unknown> & { ref: (el: HTMLElement | null) => void };
  /** The mouse and touch starters alone, for a whole row that must stay a link. */
  pointerHandle: Record<string, unknown>;
}

export function SortableItem({ id, children }: { id: string; children: (s: SortableRender) => ReactNode }) {
  const { swallowClick } = useContext(SortableCtx);
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 2 : undefined,
    position: isDragging ? "relative" : undefined,
  };
  return (
    <>
      {children({
        isDragging,
        item: {
          ref: setNodeRef,
          style,
          onClickCapture: (e) => {
            if (swallowClick()) {
              e.preventDefault();
              e.stopPropagation();
            }
          },
          // an <img> or <a> inside would start a native drag on the first move
          onDragStart: (e) => e.preventDefault(),
        },
        handle: { ref: setActivatorNodeRef, ...attributes, ...listeners },
        pointerHandle: Object.fromEntries(Object.entries(listeners ?? {}).filter(([k]) => k !== "onKeyDown")),
      })}
    </>
  );
}
