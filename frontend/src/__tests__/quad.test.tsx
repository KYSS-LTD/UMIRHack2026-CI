import { render } from "@testing-library/react";
import { BoxMarks } from "../components/desk/BoxMarks";
import { PaperViewer } from "../components/desk/PaperViewer";
import { SnippetCrop } from "../components/desk/SnippetCrop";
import { scaleBoxes } from "../lib/annotate";
import { unionBoxes } from "../lib/format";
import { pointsAttr, quadOf } from "../lib/quad";
import type { Box } from "../types";
import { ToastProvider } from "../ui/Toasts";

/** Строка на снимке, сделанном под углом около 12°: описанный прямоугольник заметно больше самой строки. */
const slanted: Box = { page: 0, x0: 100, y0: 100, x1: 500, y1: 240, quad: [[100, 140], [480, 100], [500, 200], [120, 240]] };
const plain: Box = { page: 0, x0: 50, y0: 400, x1: 300, y1: 440 };

describe("quadOf / pointsAttr", () => {
  it("returns the four corners only when they are sane", () => {
    expect(quadOf(slanted)).toHaveLength(4);
    expect(quadOf(plain)).toBeNull();
    expect(quadOf({ ...plain, quad: [[1, 2], [3, 4]] as [number, number][] })).toBeNull(); // не четыре угла
    expect(quadOf({ ...plain, quad: [[1, 2], [3, 4], [5, 6], [NaN, 8]] })).toBeNull(); // битые числа — обычный прямоугольник
  });

  it("writes points for svg", () => {
    expect(pointsAttr([[1, 2], [3.04, 4.06], [5, 6], [7, 8]])).toBe("1,2 3,4.1 5,6 7,8");
  });
});

describe("BoxMarks", () => {
  const draw = (boxes: Box[], kind: "dim" | "hover" | "hit" = "hit") => render(<div className="canvas"><BoxMarks boxes={boxes} kind={kind} keyPrefix="k" width={1200} height={1700} /></div>);

  it("draws a straight scan with plain rectangles and no svg", () => {
    const { container } = draw([plain]);
    expect(container.querySelectorAll("span.box.hit")).toHaveLength(1);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("draws a tilted line as a polygon along the line, not as a rectangle around it", () => {
    const { container } = draw([slanted, plain], "hit");
    expect(container.querySelectorAll("span.box.hit")).toHaveLength(1); // прямая рамка осталась прямоугольником
    const svg = container.querySelector("svg.quad-layer")!;
    expect(svg).toHaveAttribute("viewBox", "0 0 1200 1700");
    const poly = svg.querySelectorAll("polygon.quad.hit");
    expect(poly).toHaveLength(1);
    expect(poly[0]).toHaveAttribute("points", "100,140 480,100 500,200 120,240");
  });

  it("renders nothing for an empty list", () => {
    const { container } = draw([]);
    expect(container.querySelector(".canvas")!.children).toHaveLength(0);
  });
});

describe("SnippetCrop on a tilted scan", () => {
  it("lays the polygon over the crop in the pixels of the photo", () => {
    const { container } = render(<SnippetCrop src="blob:x" width={1000} height={1400} boxes={[slanted]} label="вуз" />);
    expect(container.querySelector(".snippet-box")).toBeNull();
    const svg = container.querySelector("svg.snippet-quads")!;
    const [x, y, w, h] = svg.getAttribute("viewBox")!.split(" ").map(Number);
    expect(x).toBeLessThanOrEqual(100);
    expect(y).toBeLessThanOrEqual(100);
    expect(x! + w!).toBeGreaterThanOrEqual(500);
    expect(y! + h!).toBeGreaterThanOrEqual(240);
    expect(svg.querySelector("polygon")).toHaveAttribute("points", "100,140 480,100 500,200 120,240");
  });

  it("keeps rectangles for a straight scan", () => {
    const { container } = render(<SnippetCrop src="blob:x" width={1000} height={1400} boxes={[plain]} label="вуз" />);
    expect(container.querySelector(".snippet-box")).not.toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("PaperViewer on a tilted scan", () => {
  it("highlights the line with a polygon", () => {
    const props = {
      src: "data:image/jpeg;base64,AAAA",
      pages: null,
      fileName: "photo.jpg",
      pdf: false,
      scanning: false,
      progress: 0,
      stageLabel: "",
      highlight: { id: "u1", label: "Университет", boxes: [slanted] },
      hover: null,
      allBoxes: [slanted],
      showAll: true,
      revealKey: "t1:done",
      onToggleAll: () => {},
      onClear: () => {},
      docs: [],
      activeDoc: 0,
      busy: false,
      onSelectDoc: () => {},
      onAnother: () => {},
    };
    const { container } = render(
      <ToastProvider>
        <PaperViewer {...props} />
      </ToastProvider>,
    );
    expect(container.querySelector(".canvas polygon.quad.hit")).not.toBeNull();
    expect(container.querySelector(".canvas polygon.quad.dim")).not.toBeNull();
    expect(container.querySelector(".canvas span.box.hit")).toBeNull();
  });
});

describe("annotate / unionBoxes", () => {
  it("scales the quad together with the box", () => {
    const [r] = scaleBoxes([slanted], 0, 1000, 500, 1000, 500);
    expect(r!.quad).toEqual([[50, 70], [240, 50], [250, 100], [60, 120]]);
    const [flat] = scaleBoxes([plain], 0, 1000, 500, 1000, 500);
    expect(flat).toEqual({ x: 25, y: 200, w: 125, h: 20 });
    expect("quad" in flat!).toBe(false);
  });

  it("the union for the camera is a plain box and never inherits a quad", () => {
    const u = unionBoxes([slanted, plain])!;
    expect(u).toEqual({ page: 0, x0: 50, y0: 100, x1: 500, y1: 440 });
    expect("quad" in unionBoxes([slanted])!).toBe(false);
  });
});
