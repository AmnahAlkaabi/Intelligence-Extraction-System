import { useRef, useState } from "react";
import type { ChatMessage, Citation } from "../api/types";
import { sendChatMessage } from "../api/client";

interface DisplayMessage extends ChatMessage {
  citations?: Citation[];
  uncertain?: boolean;
}

export function ChatPanel({ jobId }: { jobId: string }) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    const history: ChatMessage[] = messages.map(({ role, content }) => ({ role, content }));
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setSending(true);
    try {
      const resp = await sendChatMessage(jobId, text, history);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: resp.answer, citations: resp.citations, uncertain: resp.uncertain },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${e instanceof Error ? e.message : "chat failed"}`, uncertain: true },
      ]);
    } finally {
      setSending(false);
      setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }), 50);
    }
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <p className="muted">
            Ask questions about the analyzed documents — answers are grounded in the retrieved
            text and knowledge graph, with citations back to source files.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <div className="chat-msg-bubble">
              {m.content}
              {m.uncertain && <span className="uncertain-flag">⚠ low confidence</span>}
            </div>
            {m.citations && m.citations.length > 0 && (
              <div className="chat-citations">
                {m.citations.map((c, ci) => (
                  <details key={ci} className="citation">
                    <summary>{c.source_file.split("/").pop()}{c.page ? ` p.${c.page}` : ""}</summary>
                    <p>{c.chunk_text}</p>
                  </details>
                ))}
              </div>
            )}
          </div>
        ))}
        {sending && <div className="chat-msg chat-msg-assistant"><div className="chat-msg-bubble typing">…</div></div>}
      </div>
      <div className="chat-input-row">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="Ask about the extracted intelligence…"
        />
        <button className="btn-primary" onClick={send} disabled={sending || !input.trim()}>Send</button>
      </div>
    </div>
  );
}
