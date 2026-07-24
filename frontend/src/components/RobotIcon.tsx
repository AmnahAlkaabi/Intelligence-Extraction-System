export function RobotIcon({ className, visorClassName }: { className?: string; visorClassName?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <line x1="16" y1="3" x2="16" y2="8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="16" cy="3" r="1.7" fill="currentColor" />
      <rect x="5" y="8" width="22" height="18" rx="5" stroke="currentColor" strokeWidth="2.4" />
      <rect className={visorClassName} x="9" y="15" width="14" height="3.4" rx="1.7" fill="currentColor" />
      <line x1="2" y1="14" x2="5" y2="14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <line x1="27" y1="14" x2="30" y2="14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}
