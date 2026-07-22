import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import type { ChatMessage, ChatSummary } from "../api/types";
import { Spinner } from "../components/Spinner";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel } from "../components/ui/Panel";
import { useChat, useChats, useCreateChat, useDeleteChat, useStreamingSend } from "../hooks/useChats";
import { errorMessage, formatDateTime } from "../lib/format";

const EXAMPLES = [
  "How many reports do we have?",
  "How many findings relate to SQL injection?",
  "Which report has the most critical findings?",
  "What's the latest verdict on the IDOR finding?",
];

/** One conversation bubble — the operator's question or the assistant's answer. */
function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={
          isUser
            ? "max-w-[80%] rounded-2xl bg-iris/12 px-3.5 py-2 text-[13px] text-fg ring-1 ring-inset ring-iris/25"
            : "max-w-[88%] whitespace-pre-wrap rounded-2xl bg-panel-2 px-3.5 py-2 text-[13px] leading-relaxed text-fg"
        }
      >
        {message.content}
      </div>
    </div>
  );
}

/** The animated "assistant is thinking" placeholder (local models can be slow). */
function Thinking() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1.5 rounded-2xl bg-panel-2 px-3.5 py-2.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 animate-bounce rounded-full bg-dim"
            style={{ animationDelay: `${String(i * 120)}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

/** The message input: a textarea that sends on Enter (Shift+Enter for a newline). */
function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <form
      className="flex items-end gap-2 border-t border-line px-3 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        value={text}
        rows={1}
        aria-label="Ask about the reports"
        placeholder="Ask about your reports, findings, or verdicts…"
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        className="max-h-40 min-h-[38px] flex-1 resize-none rounded-lg border border-line bg-panel-2 px-3 py-2 text-[13px] text-fg placeholder:text-faint focus:border-iris/60"
      />
      <Button type="submit" disabled={disabled || !text.trim()}>
        Send
      </Button>
    </form>
  );
}

/** The active thread: transcript + composer, with an optimistic pending turn. */
function Conversation({ chatId }: { chatId: number }) {
  const chat = useChat(chatId);
  const { send, isStreaming, streamed, error } = useStreamingSend(chatId);
  const [pending, setPending] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const messages = chat.data?.messages ?? [];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, pending, streamed]);

  function handleSend(text: string) {
    setPending(text);
    void send(text).finally(() => {
      setPending(null);
    });
  }

  if (chat.isPending) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Spinner label="Loading conversation" />
      </div>
    );
  }
  if (chat.isError) {
    return (
      <p role="alert" className="p-6 text-[13px] text-danger-fg">
        {errorMessage(chat.error)}
      </p>
    );
  }

  const empty = messages.length === 0 && pending === null && !isStreaming;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {empty ? (
          <div className="mx-auto mt-8 max-w-md text-center">
            <p className="text-[13px] text-dim">
              Ask the read-only assistant anything about your ingested reports,
              findings, and retest verdicts.
            </p>
            <div className="mt-4 space-y-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    handleSend(example);
                  }}
                  className="block w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[12px] text-dim transition-colors hover:border-iris/40 hover:text-fg"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <Bubble key={message.id} message={message} />
            ))}
            {pending !== null && (
              <Bubble
                message={{ id: -1, role: "user", content: pending, created_at: "" }}
              />
            )}
            {isStreaming &&
              (streamed ? (
                <Bubble
                  message={{ id: -2, role: "assistant", content: streamed, created_at: "" }}
                />
              ) : (
                <Thinking />
              ))}
          </>
        )}
        {error && (
          <p role="alert" className="text-[12px] text-danger-fg">
            {errorMessage(error)}
          </p>
        )}
        <div ref={endRef} />
      </div>
      <Composer disabled={isStreaming} onSend={handleSend} />
    </div>
  );
}

/** The left rail: new-chat button + the list of persisted threads. */
function ThreadList({ activeId }: { activeId: number | undefined }) {
  const chats = useChats();
  const create = useCreateChat();
  const remove = useDeleteChat();
  const navigate = useNavigate();

  function startChat() {
    create.mutate(undefined, {
      onSuccess: (chat: ChatSummary) => {
        navigate(`/chat/${String(chat.id)}`);
      },
    });
  }

  function removeChat(id: number) {
    remove.mutate(id, {
      onSuccess: () => {
        if (id === activeId) navigate("/chat");
      },
    });
  }

  return (
    <div className="flex min-h-0 flex-col border-b border-line md:border-b-0 md:border-r">
      <div className="flex items-center justify-between px-3 py-3">
        <Eyebrow>Threads</Eyebrow>
        <Button onClick={startChat} disabled={create.isPending} className="px-2.5 py-1">
          New
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {chats.isPending ? (
          <div className="px-2 py-1">
            <Spinner label="Loading" />
          </div>
        ) : (chats.data ?? []).length === 0 ? (
          <p className="px-2.5 py-1.5 text-[12px] text-faint">No chats yet.</p>
        ) : (
          (chats.data ?? []).map((chat) => (
            <div
              key={chat.id}
              className={`group flex items-center gap-1 rounded-lg px-1 ${
                chat.id === activeId ? "bg-iris/12 ring-1 ring-inset ring-iris/25" : "hover:bg-panel-2"
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  navigate(`/chat/${String(chat.id)}`);
                }}
                className="min-w-0 flex-1 px-1.5 py-2 text-left"
                title={chat.title}
              >
                <span className="block truncate text-[12px] text-fg">{chat.title}</span>
                <span className="block font-mono text-[10px] text-faint">
                  {formatDateTime(chat.updated_at)}
                </span>
              </button>
              <button
                type="button"
                aria-label={`Delete ${chat.title}`}
                onClick={() => {
                  removeChat(chat.id);
                }}
                className="shrink-0 rounded p-1 text-faint opacity-0 transition-opacity hover:text-danger-fg group-hover:opacity-100"
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M3 4h10M6.5 4V2.8h3V4M5 4l.6 9h4.8L11 4"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * `/chat` (+ `/chat/:id`): the FR-18 reports assistant — a read-only chat over the
 * whole corpus of reports, findings, and verdicts. A left rail lists persisted
 * threads; the pane shows the active conversation or a prompt to start one.
 */
export function Chat() {
  const { id } = useParams<{ id?: string }>();
  const activeId = id ? Number(id) : undefined;

  return (
    <div className="rev-rise">
      <Panel className="grid h-[calc(100vh-9rem)] grid-rows-[auto_1fr] overflow-hidden md:grid-cols-[240px_1fr] md:grid-rows-none">
        <ThreadList activeId={activeId} />
        {activeId != null ? (
          <Conversation key={activeId} chatId={activeId} />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
            <Eyebrow>Reports assistant</Eyebrow>
            <p className="max-w-sm text-[13px] text-dim">
              Start a new chat to ask questions about your reports, findings, and
              verdicts — “how many reports do we have?”, “how many findings relate
              to XSS?”, and so on.
            </p>
            <StartButton />
          </div>
        )}
      </Panel>
    </div>
  );
}

/** The empty-state "New chat" action (mirrors the rail's, kept for the centred pane). */
function StartButton() {
  const create = useCreateChat();
  const navigate = useNavigate();
  return (
    <Button
      disabled={create.isPending}
      onClick={() => {
        create.mutate(undefined, {
          onSuccess: (chat: ChatSummary) => {
            navigate(`/chat/${String(chat.id)}`);
          },
        });
      }}
    >
      New chat
    </Button>
  );
}
