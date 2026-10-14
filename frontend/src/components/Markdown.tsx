import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
export function Markdown({ text }: { text: string }) {
  return (
    <article className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </article>
  );
}
