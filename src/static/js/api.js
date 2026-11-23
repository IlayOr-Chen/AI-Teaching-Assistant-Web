/**
 * Purpose: Thin fetch wrapper for the FastAPI backend. Sends cookies (HTTP-only JWT sessions),
 *          parses JSON, and converts every non-2xx response into a typed ApiError whose message
 *          is the backend's human-readable `detail` string.
 * Inputs:  HTTP method, URL path, optional JSON body / FormData.
 * Outputs: Parsed JSON (Promise) or a thrown ApiError.
 */

export class ApiError extends Error {
    /**
     * Purpose: Error carrying the HTTP status so callers can branch on 401/403/etc.
     * Inputs:  message (string), status (number).
     * Outputs: ApiError instance.
     */
    constructor(message, status) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
    }
}

/**
 * Purpose: Extract a readable message from a FastAPI error payload
 *          (`detail` may be a string or a list of validation errors).
 * Inputs:  payload (any) parsed JSON body; fallback (string).
 * Outputs: string message.
 */
function extractMessage(payload, fallback) {
    if (!payload || payload.detail === undefined) return fallback;
    if (typeof payload.detail === 'string') return payload.detail;
    if (Array.isArray(payload.detail) && payload.detail.length) {
        return payload.detail.map((d) => d.msg || String(d)).join('; ');
    }
    return fallback;
}

/**
 * Purpose: Perform a request against the API.
 * Inputs:  method (string), url (string), body (object | FormData | undefined).
 * Outputs: Promise resolving to parsed JSON (or null for empty bodies); rejects with ApiError.
 */
async function request(method, url, body) {
    const options = { method, credentials: 'same-origin', headers: {} };
    if (body instanceof FormData) {
        options.body = body; // browser sets the multipart boundary itself
    } else if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    let response;
    try {
        response = await fetch(url, options);
    } catch (networkError) {
        throw new ApiError('Cannot reach the server. Check your connection and try again.', 0);
    }

    let payload = null;
    try {
        payload = await response.json();
    } catch (_) {
        payload = null; // empty or non-JSON body
    }

    if (!response.ok) {
        throw new ApiError(extractMessage(payload, `Request failed (${response.status}).`), response.status);
    }
    return payload;
}

export const api = {
    get: (url) => request('GET', url),
    // Body is optional: bodyless POSTs (e.g. close session, multipart-or-default-asset import) send nothing
    post: (url, body) => request('POST', url, body),
    patch: (url, body) => request('PATCH', url, body),
    upload: (url, formData) => request('POST', url, formData),
};
