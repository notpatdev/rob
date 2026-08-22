/**
 * Resolves a normalized Throne username to Throne's public creator ID and
 * display handle. Throne does not offer a documented public lookup API;
 * their web app resolves creator profile pages via a public Firestore
 * "structured query" request against project `onlywish-9d17b`. This mirrors
 * that exact request shape, matching the previously verified working
 * implementation: collection `creators`, field path `username`, preferring
 * the document's `_id` field (falling back to the Firestore document ID
 * segment) for the creator ID, and the document's `username` field for the
 * display handle (falling back to the queried username).
 *
 * The `fetchImpl` parameter makes resolution injectable for tests: unit
 * tests pass a stub instead of hitting the network.
 */

export interface ResolvedThroneCreator {
  publicCreatorId: string;
  handle: string;
}

const FIRESTORE_RUN_QUERY_URL =
  "https://firestore.googleapis.com/v1/projects/onlywish-9d17b/databases/(default)/documents:runQuery";
const CREATOR_COLLECTION_ID = "creators";
const USERNAME_FIELD_PATH = "username";

interface FirestoreValue {
  stringValue?: string;
  integerValue?: string;
}

interface FirestoreDocument {
  name?: string;
  fields?: Record<string, FirestoreValue>;
}

interface FirestoreRunQueryRow {
  document?: FirestoreDocument;
}

/** Extracts the trailing document ID segment from a Firestore resource name. */
function documentIdFromName(name: string | undefined): string | null {
  if (!name || !name.includes("/")) return null;
  const segments = name.split("/");
  const last = segments[segments.length - 1];
  return last && last.length > 0 ? last : null;
}

export async function resolveThroneCreator(
  username: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ResolvedThroneCreator | null> {
  const body = {
    structuredQuery: {
      from: [{ collectionId: CREATOR_COLLECTION_ID }],
      where: {
        fieldFilter: {
          field: { fieldPath: USERNAME_FIELD_PATH },
          op: "EQUAL",
          value: { stringValue: username },
        },
      },
      limit: 1,
    },
  };

  let response: Response;
  try {
    response = await fetchImpl(FIRESTORE_RUN_QUERY_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return null;
  }
  if (!response.ok) return null;

  let rows: unknown;
  try {
    rows = await response.json();
  } catch {
    return null;
  }
  if (!Array.isArray(rows)) return null;

  for (const rawRow of rows as FirestoreRunQueryRow[]) {
    const document = rawRow?.document;
    if (!document) continue;

    const fields = document.fields ?? {};
    const idField = fields["_id"]?.stringValue;
    const publicCreatorId = idField && idField.length > 0 ? idField : documentIdFromName(document.name);
    if (!publicCreatorId) continue;

    const handleField = fields["username"]?.stringValue;
    const handle = handleField && handleField.length > 0 ? handleField : username;

    return { publicCreatorId, handle };
  }
  return null;
}
