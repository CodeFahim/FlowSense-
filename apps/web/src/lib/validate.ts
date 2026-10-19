// Client-side format checks that mirror the server's (services/api/app/api/accounts.py). They give
// instant feedback only: the server validates again and has the final say. A well-formed email can
// still be mistyped or belong to someone else; ownership is proven by the verification email.

const EMAIL_LOCAL = /^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
const EMAIL_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const TLD = /^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;
const USERNAME = /^[a-z0-9][a-z0-9._-]{1,28}[a-z0-9]$/;
const RESERVED = new Set(["admin", "administrator", "root", "support", "help", "lifeline", "flowsense", "system", "owner", "staff", "demo", "fresh", "security", "api", "null", "undefined"]);

export function emailProblem(raw: string): string | null {
  const email = raw.trim().toLowerCase();
  if (!email) return "Enter your email address.";
  if (email.length > 254 || email.split("@").length !== 2) return "Enter a valid email address, like name@example.com.";
  const [local, domain] = email.split("@");
  const labels = domain.split(".");
  if (!local || local.length > 64 || !EMAIL_LOCAL.test(local)) return "The part before @ isn't valid. Check for spaces or unusual characters.";
  if (labels.length < 2 || !labels.every((l) => EMAIL_LABEL.test(l)) || !TLD.test(labels[labels.length - 1]))
    return "The part after @ isn't a valid domain, like gmail.com or outlook.com.";
  return null;
}

export function usernameProblem(raw: string): string | null {
  const u = raw.trim().toLowerCase();
  if (!u) return "Choose a username.";
  if (u.length < 3 || u.length > 30) return "Use 3 to 30 characters.";
  if (!USERNAME.test(u) || /[._-]{2}/.test(u)) return "Use lower-case letters, numbers, dots, dashes or underscores, starting and ending with a letter or number.";
  if (RESERVED.has(u)) return "This username is reserved. Choose another.";
  return null;
}

/** A likely typo in a popular email domain ("gmial.com"), so the user can fix it before continuing. */
export function domainSuggestion(raw: string): string | null {
  const email = raw.trim().toLowerCase();
  const at = email.lastIndexOf("@");
  if (at < 1) return null;
  const domain = email.slice(at + 1);
  const typos: Record<string, string> = {
    "gmial.com": "gmail.com", "gmal.com": "gmail.com", "gmail.co": "gmail.com", "gamil.com": "gmail.com", "gnail.com": "gmail.com", "gmail.con": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmail.co": "hotmail.com", "outlok.com": "outlook.com", "outlook.co": "outlook.com", "outllook.com": "outlook.com",
    "yahooo.com": "yahoo.com", "yaho.com": "yahoo.com", "yahoo.co": "yahoo.com",
  };
  return typos[domain] ? `${email.slice(0, at)}@${typos[domain]}` : null;
}
