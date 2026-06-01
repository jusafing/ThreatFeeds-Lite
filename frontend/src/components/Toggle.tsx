interface Props {
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}

export default function Toggle({ checked, onChange, disabled }: Props) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`toggle ${checked ? 'toggle-on' : 'toggle-off'}`}
    >
      <span
        className={`toggle-knob ${checked ? 'translate-x-4' : 'translate-x-0'}`}
      />
    </button>
  )
}
