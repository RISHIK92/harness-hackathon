/** Meeting codes are `abcd-efgh`, and nobody types the hyphen.
 *
 * The code is designed to be read aloud (no 0, O, 1, l or I in the
 * alphabet), so it arrives every way a spoken thing does: dictated without
 * the hyphen, pasted with a space, or pasted as the whole join link.
 */
export function formatMeetingCode(input: string): string {
  // People paste the link at least as often as the code.
  const tail = input.includes("/") ? input.slice(input.lastIndexOf("/") + 1) : input;
  const raw = tail
    .toLowerCase()
    .replace(/[?#].*$/, "") // a link may carry ?knock=… or a fragment
    .replace(/[^a-z0-9]/g, "") // eats the hyphen, spaces, and stray punctuation
    .slice(0, 8);

  // The hyphen only appears once there is a character after it. Adding it at
  // exactly four would make backspace unable to get past it: the keystroke
  // removes the hyphen, this puts it straight back, and the field jams.
  return raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
}
