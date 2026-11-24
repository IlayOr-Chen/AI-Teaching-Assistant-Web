/**
 * Purpose: Thin fetch wrapper for the FastAPI backend. Sends cookies (HTTP-only JWT sessions),
 *          supports optional custom request headers (such as student BYOK `X-Gemini-Api-Key` and
 *          `X-Gemini-Model`), parses JSON or plain-text error details, and converts every non-2xx
 *          response into a typed ApiError displaying the backend's actual detail message.
 * Inputs:  HTTP method, URL path, optional JSON body / FormData, and optional headers object.
 * Outputs: Parsed JSON (Promise) or a thrown ApiError.
 */

export class ApiError extends Error {
    /**
     * Purpose: Error carrying the HTTP status so callers can branch on 400/401/403/502/etc.
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
 * Purpose: Extract a readable message from a FastAPI JSON or text error payload
 *          (`detail` may be a string, list of validation errors, or structured object).
 * Inputs:  payload (any) parsed JSON body; rawText (string) raw response text; fallback (string).
 * Outputs: string message.
 */
function extractMessage(payload, rawText, fallback) {
    if (payload && payload.detail !== undefined) {
        if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail;
        if (Array.isArray(payload.detail) && payload.detail.length) {
            return payload.detail.map((d) => d.msg || (typeof d === 'string' ? d : JSON.stringify(d))).join('; ');
        }
        if (typeof payload.detail === 'object') {
            return payload.detail.message || JSON.stringify(payload.detail);
        }
    }
    if (payload && typeof payload.message === 'string' && payload.message.trim()) return payload.message;
    if (payload && typeof payload.error === 'string' && payload.error.trim()) return payload.error;
    const cleanRaw = (rawText || '').trim();
    if (cleanRaw) return cleanRaw.slice(0, 260);
    return fallback;
}

/**
 * Purpose: Perform a request against the API with optional custom HTTP headers.
 * Inputs:  method (string), url (string), body (object | FormData | undefined), extraHeaders (object).
 * Outputs: Promise resolving to parsed JSON (or null for empty bodies); rejects with ApiError.
 */
async function request(method, url, body, extraHeaders = {}) {
    const options = {
        method,
        credentials: 'same-origin',
        headers: { ...extraHeaders },
    };
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

    let rawText = '';
    let payload = null;
    try {
        rawText = await response.text();
        if (rawText) payload = JSON.parse(rawText);
    } catch (_) {
        payload = null; // empty or non-JSON body; keep rawText for error reporting
    }

    if (!response.ok) {
        throw new ApiError(
            extractMessage(payload, rawText, `Request failed (${response.status}).`),
            response.status,
        );
    }
    return payload;
}

export const api = {
    get: (url, headers = {}) => request('GET', url, undefined, headers),
    // Body is optional: bodyless POSTs (e.g. close session, multipart-or-default-asset import) send nothing
    post: (url, body, headers = {}) => request('POST', url, body, headers),
    put: (url, body, headers = {}) => request('PUT', url, body, headers),
    patch: (url, body, headers = {}) => request('PATCH', url, body, headers),
    upload: (url, formData, headers = {}) => request('POST', url, formData, headers),
};
