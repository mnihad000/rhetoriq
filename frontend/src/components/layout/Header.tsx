import { Link } from "react-router-dom";

type HeaderProps = { variant?: "default" | "landing" };

export default function Header({ variant = "default" }: HeaderProps) {
  return (
    <header className="px-4 pt-6 sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-7xl items-center justify-between border-b border-[var(--border)] pb-5">
        <Link
          to="/"
          className={
            variant === "landing"
              ? "text-2xl font-semibold tracking-normal text-[var(--ink)] transition hover:text-[var(--accent)]"
              : "font-[Iowan_Old_Style,Palatino_Linotype,Book_Antiqua,Georgia,serif] text-2xl font-semibold tracking-[-0.05em] text-[var(--ink)] transition hover:text-[var(--accent)]"
          }
        >
          RhetoriQ
        </Link>
      </div>
    </header>
  );
}
