import React from "react";

/** Tiny markdown renderer for Astra answers: **bold**, _italic_, "- " bullets, blank-line paragraphs. */
function inline(text: string, key: number): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_)/g).filter(Boolean);
  return (
    <React.Fragment key={key}>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
        if (p.startsWith("_") && p.endsWith("_") && p.length > 2) return <em key={i} className="text-muted">{p.slice(1, -1)}</em>;
        return <span key={i}>{p}</span>;
      })}
    </React.Fragment>
  );
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flushList = () => {
    if (list.length) {
      blocks.push(<ul key={`l${blocks.length}`}>{list.map((l, i) => <li key={i}>{inline(l, i)}</li>)}</ul>);
      list = [];
    }
  };
  const flushPara = () => {
    if (para.length) {
      blocks.push(<p key={`p${blocks.length}`}>{para.map((l, i) => <React.Fragment key={i}>{inline(l, i)}{i < para.length - 1 && <br />}</React.Fragment>)}</p>);
      para = [];
    }
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (/^\s*[-•]\s+/.test(line)) {
      flushPara();
      list.push(line.replace(/^\s*[-•]\s+/, ""));
    } else if (!line.trim()) {
      flushList();
      flushPara();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushList();
  flushPara();
  return <div className="prose-astra text-sm leading-relaxed">{blocks}</div>;
}
