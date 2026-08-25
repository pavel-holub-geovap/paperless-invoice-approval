export function CzechDateInput({
  value,
  disabled,
  invalid,
  field,
  onChange,
}: {
  value: string;
  disabled: boolean;
  invalid: boolean;
  field: string;
  onChange: (value: string) => void;
}) {
  return (
    <input
      data-field={field}
      aria-invalid={invalid}
      disabled={disabled}
      inputMode="numeric"
      placeholder="DD.MM.YYYY"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
