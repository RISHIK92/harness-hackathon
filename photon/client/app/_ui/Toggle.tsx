"use client";

/** A switch, in the paper palette.
 *
 * `role="switch"` on a real button rather than a styled checkbox: a checkbox
 * says "this will be submitted with a form", a switch says "this takes
 * effect now", and both places this is used take effect immediately.
 */
export default function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  /** For screen readers — the visible text sits next to the switch. */
  label: string;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className="relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors disabled:opacity-35"
      style={{
        background: checked ? "var(--l-rust)" : "rgba(28,25,23,.10)",
        border: "1px solid",
        borderColor: checked ? "var(--l-rust)" : "var(--l-rule)",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <span
        className="absolute top-1/2 block h-[16px] w-[16px] rounded-full transition-transform"
        style={{
          left: 2,
          marginTop: -8,
          background: checked ? "var(--l-paper)" : "var(--l-muted)",
          transform: `translateX(${checked ? 16 : 0}px)`,
          transitionTimingFunction: "cubic-bezier(.16,1,.3,1)",
          transitionDuration: "0.35s",
        }}
      />
    </button>
  );
}
