/**
 * Empty-state openers, per class AND subject.
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
 * covers it in the ingested corpus. The chapter is named in the comment beside
 * each block so a future edition change can be traced rather than guessed at.
 *
 * SUBJECT, NOT JUST GRADE
 *
 * Retrieval and the class-picker are both subject-aware (see curriculum.subjects()
 * and the subject menu in Chat.tsx) — Mathematics has been ingested for every
 * class alongside Science/EVS. The opener pool must key on subject too, or a
 * student who explicitly picks Mathematics still gets shown "Pressure kya hota
 * hai?", which is not just wrong, it actively tells them nothing was heard.
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

type OpenerBank = Record<number, readonly string[]>;

const EVS: OpenerBank = {
  // Class 5 — Our Wondrous World (EVS, not Science)
  5: [
    "Nadi kahan se aati hai?",
    "Khana humein energy kaise deta hai?",
    "Mausam kyun badalta hai?",
    "Kapde kaise bante hain?",
    "Prithvi hamara ghar kyun hai?",
    "Paani kyun zaroori hai?",
  ],
};

const SCIENCE: OpenerBank = {
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

const MATHEMATICS: OpenerBank = {
  // Class 5 — ch02 Fractions, ch03 Angles as Turns, ch07 Shapes and Patterns,
  // ch08 Weight and Capacity, ch10 Symmetrical Design, ch12 Racing Seconds
  5: [
    "Fraction kya hoti hai?",
    "Angle kaise banta hai?",
    "Shapes mein pattern kaise dhoondhein?",
    "Weight aur capacity mein kya farak hai?",
    "Symmetry kya hoti hai?",
    "Time kaise naapte hain?",
  ],
  // Class 6 — ch02 Lines and Angles, ch04 Data Handling, ch05 Prime Time,
  // ch06 Perimeter and Area, ch09 Symmetry, ch10 The Other Side of Zero
  6: [
    "Line aur angle mein kya farak hai?",
    "Data ko graph mein kaise dikhayein?",
    "Prime number kya hota hai?",
    "Perimeter aur area kaise nikalte hain?",
    "Symmetry kaise pehchaanein?",
    "Negative numbers kya hote hain?",
  ],
  // Class 7 — ch02 Simple Expressions, ch03 Peek Beyond the Point,
  // ch05 Parallel and Intersecting Lines, ch09 Geometric Twins,
  // ch10 Operations with Integers, ch15 Finding the Unknown
  7: [
    "Algebraic expression kya hoti hai?",
    "Decimal numbers kaise kaam karte hain?",
    "Parallel lines kaise pehchaanein?",
    "Congruent shapes kya hoti hain?",
    "Integers ko multiply kaise karein?",
    "Equation solve kaise karte hain?",
  ],
  // Class 8 — ch01 A Square and a Cube, ch02 Power Play, ch04 Quadrilaterals,
  // ch09 The Baudhayana-Pythagoras Theorem, ch10 Proportional Reasoning,
  // ch13 Algebra Play
  8: [
    "Square aur cube mein kya farak hai?",
    "Exponent ka matlab kya hota hai?",
    "Quadrilateral kise kehte hain?",
    "Pythagoras theorem kya kehta hai?",
    "Ratio aur proportion mein kya farak hai?",
    "Algebra ke basic rules kya hain?",
  ],
  // Class 9 — ch01 Orienting Yourself (Coordinates), ch02 Linear Polynomials,
  // ch03 The World of Numbers, ch04 Algebraic Identities, ch07 Probability,
  // ch08 Sequences and Progressions
  9: [
    "Coordinate geometry kya hoti hai?",
    "Polynomial kya hota hai?",
    "Real numbers kya hote hain?",
    "Algebraic identities kya hoti hain?",
    "Probability kaise calculate karte hain?",
    "Sequence aur progression mein kya farak hai?",
  ],
  // Class 10 — ch04 Quadratic Equations, ch05 Arithmetic Progressions,
  // ch06 Triangles, ch08 Introduction to Trigonometry, ch10 Circles,
  // ch13 Statistics
  10: [
    "Quadratic equation kaise solve karte hain?",
    "AP (Arithmetic Progression) kya hoti hai?",
    "Similar triangles kaise pehchaanein?",
    "Trigonometry mein sin cos kya hota hai?",
    "Circle ki tangent kya hoti hai?",
    "Mean, median aur mode mein kya farak hai?",
  ],
};

/** Keyed by the exact subject string the corpus and curriculum.subjects() use. */
const BANKS: Record<string, OpenerBank> = { EVS, Science: SCIENCE, Mathematics: MATHEMATICS };

/** Subject preference when the chat has no subject pinned yet ("All subjects").
 *  EVS first because Class 5 has no Science bank; Science next because that was
 *  the only subject in the corpus before Mathematics was ingested, so an
 *  unscoped chat keeps its original behaviour rather than changing underfoot. */
const UNSCOPED_ORDER = ["EVS", "Science", "Mathematics"];

/** Three openers for this class and subject. Falls back through: the requested
 *  subject's bank for this grade -> the unscoped preference order for this grade
 *  -> Class 8 Science -> because showing nothing is worse than showing something
 *  plausible, but showing the wrong SUBJECT's questions after the student picked
 *  one explicitly is the exact bug this file exists to avoid. */
export function openersFor(grade: number | undefined, subject?: string): string[] {
  const g = grade ?? 8;
  const pool =
    (subject ? BANKS[subject]?.[g] : undefined) ??
    UNSCOPED_ORDER.map((s) => BANKS[s]?.[g]).find((bank) => bank !== undefined) ??
    SCIENCE[8]!;
  const picked = [...pool];
  // Fisher-Yates, so the three shown are not always the first three.
  for (let i = picked.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [picked[i], picked[j]] = [picked[j]!, picked[i]!];
  }
  return picked.slice(0, 3);
}
