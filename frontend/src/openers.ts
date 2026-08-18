/**
 * Empty-state openers, per class.
 *
 * The chat's first screen used three hardcoded prompts — "Pressure kya hota hai?",
 * "Cyclone kaise banta hai?", "Friction samjhao" — which are Class 8 Science.
 * A Class 5 or Class 10 student's very first impression was three questions from
 * someone else's textbook.
 *
 * WHY THESE ARE HAND-WRITTEN AND NOT DERIVED FROM CHAPTER TITLES
 *
 * The obvious alternative is a `GET /v1/curriculum/chapters?grade=` endpoint and
 * a template — "{title} samjhao". It produces "The Wonderful World of Science
 * samjhao" and "Our Home: Earth, a Unique Life Sustaining Planet samjhao". A
 * chapter title is a heading, not a question a twelve-year-old would type, and
 * the opener's whole job is to look like something they'd actually ask.
 *
 * So: written by hand, but every one of them checked against the chapter that
 * covers it in the ingested corpus (73 chapters across classes 5-10). The chapter
 * is named in the comment beside each block so a future edition change can be
 * traced rather than guessed at.
 *
 * THE DRIFT RISK, STATED
 *
 * These are a second copy of curriculum knowledge, and copies rot. When NCERT
 * revises a book, a prompt here can silently start pointing at a chapter that no
 * longer exists — which is exactly what happened with the original
 * "Coal kaise banta hai?", a question the 2025 Curiosity edition dropped, so the
 * first tap taught the child that GuruJi does not know things.
 *
 * The guard is eval_retrieval.py: every prompt below is also a row in the eval
 * set, so a stale one shows up as a recall miss rather than as a bad first
 * impression. Re-check this file whenever a class is re-ingested.
 */

/** Six per class; three are shown, chosen at random per new chat, so the empty
 *  state does not look identical every single time a student opens it. */
export const OPENERS: Record<number, readonly string[]> = {
  // Class 5 — Our Wondrous World (EVS, not Science)
  5: [
    "Nadi kahan se aati hai?",
    "Khana humein energy kaise deta hai?",
    "Mausam kyun badalta hai?",
    "Kapde kaise bante hain?",
    "Prithvi hamara ghar kyun hai?",
    "Paani kyun zaroori hai?",
  ],
  // Class 6 — Curiosity: ch04 Magnets, ch07 Temperature, ch08 States of Water,
  // ch09 Separation, ch11 Nature's Treasures, ch12 Beyond Earth
  6: [
    "Magnet kaise kaam karta hai?",
    "Temperature kaise naapte hain?",
    "Paani bhaap kaise banta hai?",
    "Mixture ko alag kaise karein?",
    "Chand raat mein kyun dikhta hai?",
    "Prakriti se humein kya milta hai?",
  ],
  // Class 7 — ch02 Acidic/Basic, ch03 Circuits, ch05 Changes, ch07 Heat,
  // ch10 Life Processes in Plants, ch11 Light
  7: [
    "Acid aur base mein kya farak hai?",
    "Electric circuit kaise banta hai?",
    "Physical aur chemical change samjhao",
    "Garmi ek jagah se doosri jagah kaise jaati hai?",
    "Plants apna khana kaise banate hain?",
    "Shadow kaise banti hai?",
  ],
  // Class 8 — ch05 Forces, ch06 Pressure, ch07 Particulate Matter,
  // ch09 Solutions, ch10 Light, ch12 Nature in Harmony
  8: [
    "Pressure kya hota hai?",
    "Cyclone kaise banta hai?",
    "Friction samjhao",
    "Cheeni paani mein kyun ghul jaati hai?",
    "Concave aur convex mirror mein farak?",
    "Food chain kya hoti hai?",
  ],
  // Class 9 — Exploration: ch02 Cell, ch04 Motion, ch06 Forces, ch07 Work,
  // ch08 Atom, ch10 Sound
  9: [
    "Cell kya hota hai?",
    "Motion ko kaise describe karte hain?",
    "Newton ke laws samjhao",
    "Work aur energy mein kya farak hai?",
    "Atom ke andar kya hota hai?",
    "Sound kaise travel karti hai?",
  ],
  // Class 10 — ch01 Chemical Reactions, ch02 Acids/Bases/Salts, ch05 Life
  // Processes, ch09 Light, ch11 Electricity, ch13 Our Environment
  10: [
    "Chemical equation balance kaise karein?",
    "Acid, base aur salt samjhao",
    "Life processes kya hote hain?",
    "Reflection aur refraction mein farak?",
    "Ohm's law kya kehta hai?",
    "Ecosystem kaise kaam karta hai?",
  ],
};

/** Three openers for this class. Falls back to Class 8 only when the grade is
 *  genuinely unknown — a brand-new browser that has not resolved the profile
 *  yet — because showing nothing is worse than showing something plausible. */
export function openersFor(grade: number | undefined): string[] {
  const pool = OPENERS[grade ?? 0] ?? OPENERS[8]!;
  const picked = [...pool];
  // Fisher-Yates, so the three shown are not always the first three.
  for (let i = picked.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [picked[i], picked[j]] = [picked[j]!, picked[i]!];
  }
  return picked.slice(0, 3);
}
