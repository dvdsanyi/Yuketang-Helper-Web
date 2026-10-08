// Long names keep their start and end visible ("Introduction to the…Periods 3-4)")
// instead of wrapping. The full name shows on hover, but only when it was cut.
export default function MiddleTruncate({ text, className = '' }: { text: string; className?: string }) {
  const split = text.length - Math.min(8, Math.floor(text.length / 3))
  return (
    <span
      className={`truncate-middle ${className}`}
      onMouseEnter={(e) => {
        const head = e.currentTarget.firstElementChild as HTMLElement
        e.currentTarget.title = head.scrollWidth > head.clientWidth ? text : ''
      }}
    >
      <span className="truncate-head">{text.slice(0, split)}</span>
      <span className="truncate-tail">{text.slice(split)}</span>
    </span>
  )
}
