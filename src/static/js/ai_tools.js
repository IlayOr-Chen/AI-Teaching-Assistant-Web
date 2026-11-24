/**
 * Purpose: Frontend UI module for Feature 4 (ML Lecture Video Summary — MP4 Only + Gemini Files API
 *          with Lecturer API key & valid model settings) and Feature 5 (Deep Homework Review &
 *          Peer Comparison — Multi-Format Files & Public Git Repo URLs + Gemini + Gated Release Rule).
 * Inputs:  DOM container and `{ user, role }` context.
 * Outputs: Interactive Video AI and Homework tabs.
 */

import { api } from './api.js';
import { icon } from './icons.js';
import {
    el, mount, toast, button, withLoading, formatTime,
} from './ui.js';

const LESSON_TAGS = ['Lesson 1', 'Lesson 2', 'Lesson 3', 'Lesson 4', 'Lesson 5', 'General'];

const VALID_GEMINI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-2.5-pro',
    'gemini-2.0-flash',
];

const HOMEWORK_FILE_ACCEPT = [
    '.pdf', '.docx', '.txt', '.md', '.zip',
    '.py', '.java', '.c', '.cpp', '.h',
    '.js', '.ts', '.html', '.css', '.json',
].join(',');

/* ================================================================== */
/* Feature 4 — ML Lecture Video Summary (MP4 Only + Gemini Files API) */
/* ================================================================== */

/**
 * Purpose: Render the Lecturer's Gemini Settings panel + MP4 Lecture Video Summarizer tab.
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: void.
 */
export function renderVideoSummaryTab(container, ctx) {
    const settingsStatusChip = el('span', { class: 'chip' }, 'Loading settings…');
    const apiKeyInput = el('input', {
        type: 'password',
        placeholder: 'Enter or update your Google Gemini API key…',
        autocomplete: 'off',
    });

    const modelSelect = el(
        'select',
        { 'aria-label': 'Select Gemini Model' },
        [
            ...VALID_GEMINI_MODELS.map((m) => el('option', { value: m, selected: m === 'gemini-2.5-flash' }, m)),
            el('option', { value: '__custom__' }, 'Custom model ID…'),
        ],
    );
    const customModelInput = el('input', {
        type: 'text',
        placeholder: 'Enter custom Gemini model ID (e.g. gemini-2.5-flash)',
        class: 'hidden',
    });

    modelSelect.addEventListener('change', () => {
        const isCustom = modelSelect.value === '__custom__';
        customModelInput.classList.toggle('hidden', !isCustom);
        if (isCustom) customModelInput.focus();
    });

    const getSelectedModelId = () => (
        modelSelect.value === '__custom__'
            ? (customModelInput.value.trim() || 'gemini-2.5-flash')
            : modelSelect.value
    );

    const setSelectedModelId = (modelId) => {
        const clean = (modelId || 'gemini-2.5-flash').trim();
        if (VALID_GEMINI_MODELS.includes(clean)) {
            modelSelect.value = clean;
            customModelInput.classList.add('hidden');
        } else {
            modelSelect.value = '__custom__';
            customModelInput.value = clean;
            customModelInput.classList.remove('hidden');
        }
    };

    const saveSettingsBtn = button('Save Gemini Settings', { cls: 'primary sm', type: 'submit', iconName: 'sparkles' });

    const loadSettings = async () => {
        try {
            const s = await api.get('/api/settings/gemini');
            setSelectedModelId(s.model_id || 'gemini-2.5-flash');
            if (s.has_api_key) {
                settingsStatusChip.className = 'chip ok';
                settingsStatusChip.textContent = `Key active (${s.masked_api_key}) · Model: ${s.model_id}`;
            } else {
                settingsStatusChip.className = 'chip danger';
                settingsStatusChip.textContent = 'GEMINI_API_KEY missing — enter key above or in .env';
            }
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    const settingsForm = el('form', { class: 'stack' }, [
        el('div', { class: 'grid grid-2' }, [
            el('label', { class: 'field' }, ['Gemini API Key (stored in Firebase RTDB /settings/gemini)', apiKeyInput]),
            el('label', { class: 'field' }, ['Gemini Model ID', modelSelect, customModelInput]),
        ]),
        el('div', { class: 'row' }, [
            saveSettingsBtn,
            ...VALID_GEMINI_MODELS.map((m) => button(m, {
                cls: 'sm ghost',
                onclick: () => setSelectedModelId(m),
            })),
            el('span', { class: 'spacer' }),
            settingsStatusChip,
        ]),
    ]);

    settingsForm.addEventListener('submit', (e) => {
        e.preventDefault();
        withLoading(saveSettingsBtn, async () => {
            const payload = { model_id: getSelectedModelId() };
            if (apiKeyInput.value.trim()) payload.api_key = apiKeyInput.value.trim();
            await api.put('/api/settings/gemini', payload);
            apiKeyInput.value = '';
            toast(`Gemini settings updated (model: ${payload.model_id}).`, 'success');
            await loadSettings();
        });
    });

    /* ----- MP4 Upload & Summary List ----- */
    const lessonSelect = el('select', {}, LESSON_TAGS.map((t) => el('option', { value: t }, t)));
    const notesInput = el('input', {
        type: 'text',
        placeholder: 'Optional lecture context notes…',
    });
    const mp4FileInput = el('input', { type: 'file', accept: '.mp4,video/mp4', class: 'hidden' });
    const uploadMp4Btn = button('Select & Analyze MP4 Video (.mp4 only)', { cls: 'primary', iconName: 'video' });
    const processingHost = el('div', { class: 'hidden' });
    const summariesHost = el('div', { class: 'stack' });

    const renderSummaryCard = (s) => {
        const del = s.delivery_insights || {};
        return el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip ok' }, s.lesson_tag || 'Lesson 1'),
                el('strong', { dir: 'auto' }, s.filename),
                el('span', { class: 'faint' }, `${s.file_size_kb} KB · Model: ${s.model_used} · ${formatTime(s.created_at)}`),
                el('span', { class: 'spacer' }),
                el('span', { class: 'chip ok' }, `Delivery: ${del.overall_rating || 'Excellent'}`),
            ]),
            el('div', { class: 'banner info' }, [
                el('span', { html: icon('sparkles') }),
                el('span', {}, s.executive_summary),
            ]),
            el('div', { class: 'grid grid-2' }, [
                el('div', { class: 'review-col' }, [
                    el('h3', {}, 'Key Topics Covered'),
                    el('ul', { class: 'bullet-list' }, (s.key_topics || []).map((t) => el('li', {}, t))),
                    el('h3', { style: 'margin-top:0.5rem' }, 'Core Learning Takeaways'),
                    el('ul', { class: 'bullet-list' }, (s.core_takeaways || []).map((t) => el('li', {}, t))),
                ]),
                el('div', { class: 'review-col peer-col' }, [
                    el('h3', {}, 'Action Items & Next Steps'),
                    el('ul', { class: 'bullet-list' }, (s.action_items || []).map((t) => el('li', {}, t))),
                    el('h3', { style: 'margin-top:0.5rem' }, 'Lecturer Delivery Insights'),
                    el('div', { class: 'delivery-grid' }, [
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Pacing'), el('div', { class: 'faint' }, del.pacing || '-')]),
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Clarity'), el('div', { class: 'faint' }, del.clarity || '-')]),
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Audio Quality'), el('div', { class: 'faint' }, del.audio_quality || '-')]),
                    ]),
                ]),
            ]),
        ]);
    };

    const loadSummaries = async () => {
        try {
            const { summaries } = await api.get('/api/video/summaries');
            if (!summaries.length) {
                mount(summariesHost, el('div', { class: 'card empty' },
                    'No lecture video summaries yet. Upload an MP4 (.mp4) recording above for an accurate Gemini teaching & delivery summary.'));
                return;
            }
            mount(summariesHost, summaries.map(renderSummaryCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    uploadMp4Btn.addEventListener('click', () => mp4FileInput.click());
    mp4FileInput.addEventListener('change', () => {
        const file = mp4FileInput.files[0];
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.mp4')) {
            toast('Unsupported video format. Only MP4 (.mp4) lecture video files are accepted.', 'error');
            mp4FileInput.value = '';
            return;
        }
        const form = new FormData();
        form.append('file', file);
        const activeModel = getSelectedModelId();

        mount(processingHost, el('div', { class: 'processing-pulse' }, [
            el('span', { class: 'spinner' }),
            el('div', { class: 'stack', style: 'gap:0.15rem' }, [
                el('strong', {}, `Uploading & analyzing video with Gemini (${activeModel})…`),
                el('span', { class: 'faint' }, `Uploading "${file.name}" (${(file.size / (1024 * 1024)).toFixed(1)} MB) via the Gemini Files API, waiting for ACTIVE processing state, and summarizing the actual lecture content…`),
            ]),
        ]));
        processingHost.classList.remove('hidden');

        withLoading(uploadMp4Btn, async () => {
            try {
                const url = `/api/video/summarize?lesson_tag=${encodeURIComponent(lessonSelect.value)}&lecturer_notes=${encodeURIComponent(notesInput.value.trim())}`;
                await api.upload(url, form);
                toast(`MP4 lecture summary generated for "${file.name}"!`, 'success');
                notesInput.value = '';
                await loadSummaries();
            } finally {
                processingHost.classList.add('hidden');
                mp4FileInput.value = '';
            }
        });
    });

    mount(container, el('div', { class: 'stack' }, [
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('sparkles') }),
                el('h2', {}, 'Lecturer Gemini AI Settings (API Key & Model ID)'),
            ]),
            settingsForm,
        ]),
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('video') }),
                el('h2', {}, 'ML Lecture Video Summarizer (MP4 Only · Gemini Files API)'),
            ]),
            el('p', { class: 'muted' }, 'Uploads MP4 (.mp4) lecture recordings (including large 100+ MB files and Hebrew filenames) via the official Gemini Files API to summarize the actual spoken and visual lecture content.'),
            el('div', { class: 'grid', style: 'grid-template-columns:1fr 2fr' }, [
                el('label', { class: 'field' }, ['Lesson Tag', lessonSelect]),
                el('label', { class: 'field' }, ['Lecture Focus / Context Notes', notesInput]),
            ]),
            el('div', { class: 'row' }, [uploadMp4Btn, mp4FileInput]),
            processingHost,
        ]),
        summariesHost,
    ]));

    loadSettings();
    loadSummaries();
}

/* ================================================================== */
/* Feature 5 — Deep Homework Review (Multi-Format Files + Git Repo)   */
/* ================================================================== */

/**
 * Purpose: Render the two-column Polished Review UI Layout:
 *          1) Concise Review & Summary Card (Strengths, Areas to Improve, Actionable Recommendations, Brief Summary),
 *          2) Dedicated Peer Comparison Column (or First-Submission Edge Case notice when only 1 student submitted).
 * Inputs:  payload (object) response from `GET /api/homework/assignments/{id}/review`.
 * Outputs: HTMLElement.
 */
function renderReviewAndComparisonColumns(payload) {
    if (!payload.review_released) {
        return el('div', { class: 'banner warn' }, [
            el('span', { html: icon('lock') }),
            el('span', {}, payload.gated_message || 'Reviews and peer comparisons are locked until the submission deadline passes or the lecturer ends submissions.'),
        ]);
    }

    const rev = payload.review || {};
    const comp = payload.peer_comparison || {};

    let peerContent;
    if (comp.is_first_submission_only) {
        peerContent = [
            el('div', { class: 'banner info' }, [
                el('span', { html: icon('info') }),
                el('span', {}, comp.notice),
            ]),
        ];
    } else {
        peerContent = [
            el('p', { class: 'faint' }, comp.notice || `Compared against ${comp.peer_count} peer submission(s).`),
            el('h3', {}, 'Relative Strengths vs. Class Peers'),
            el('ul', { class: 'bullet-list' }, (comp.relative_strengths || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Unique Ideas & Distinctive Angles'),
            el('ul', { class: 'bullet-list' }, (comp.unique_ideas || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Common Cohort Gaps'),
            el('ul', { class: 'bullet-list' }, (comp.common_gaps || []).map((s) => el('li', {}, s))),
        ];
    }

    return el('div', { class: 'review-columns' }, [
        el('div', { class: 'review-col' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip ok' }, '1 · Concise Review & Summary Card'),
                el('span', { class: 'faint' }, `${payload.filename || 'submission'} · ${payload.word_count} words`),
            ]),
            el('p', {}, rev.brief_summary || ''),
            el('h3', {}, 'Strengths'),
            el('ul', { class: 'bullet-list' }, (rev.strengths || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Areas to Improve'),
            el('ul', { class: 'bullet-list' }, (rev.areas_to_improve || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Actionable Recommendations'),
            el('ul', { class: 'bullet-list' }, (rev.actionable_recommendations || []).map((s) => el('li', {}, s))),
        ]),
        el('div', { class: 'review-col peer-col' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip info' }, '2 · Dedicated Peer Comparison Column'),
                el('span', { class: 'faint' }, `${comp.total_submissions || 1} total submission(s)`),
            ]),
            ...peerContent,
        ]),
    ]);
}

/**
 * Purpose: Render the Deep Homework Review & Peer Comparison tab for Lecturer or Student.
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: void.
 */
export function renderHomeworkTab(container, ctx) {
    const isLecturer = ctx.role === 'lecturer';
    const assignmentsHost = el('div', { class: 'stack' });

    const renderAssignmentCard = (a) => {
        const isReleased = Boolean(a.review_released);
        const deadlineStr = new Date(a.deadline_ts * 1000).toLocaleString();
        const detailHost = el('div', { class: 'stack' });

        /* ----- Lecturer Controls: "End Submission Now" + Inspect Student Reviews ----- */
        let lecturerSection = null;
        if (isLecturer) {
            const endNowBtn = button('End Submission Now', {
                cls: `sm ${isReleased ? 'ghost' : 'danger'}`,
                iconName: 'stop',
            });
            endNowBtn.disabled = isReleased;
            endNowBtn.addEventListener('click', () => withLoading(endNowBtn, async () => {
                await api.post(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/end-now`);
                toast('Submissions ended! AI Reviews and Peer Comparisons are now unlocked for all students.', 'success');
                await loadAssignments();
            }));

            const studentPills = (a.submitted_students || []).map((stu) => {
                const inspectBtn = button(`${stu.student_name} (${stu.filename})`, { cls: 'sm', iconName: 'file' });
                inspectBtn.addEventListener('click', () => withLoading(inspectBtn, async () => {
                    const revData = await api.get(
                        `/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/review?student_id=${encodeURIComponent(stu.student_id)}`,
                    );
                    mount(detailHost, renderReviewAndComparisonColumns(revData));
                }));
                return inspectBtn;
            });

            lecturerSection = el('div', { class: 'stack' }, [
                el('div', { class: 'row' }, [
                    endNowBtn,
                    el('span', { class: 'faint' }, isReleased
                        ? 'Gate status: UNLOCKED (Students can view AI review & peer comparison)'
                        : 'Gate status: LOCKED (Students can upload files or submit GitHub repo URLs; reviews remain gated until deadline or End Submission Now)'),
                ]),
                studentPills.length
                    ? el('div', { class: 'stack', style: 'gap:0.4rem' }, [
                        el('span', { class: 'faint' }, isReleased
                            ? 'Click a student submission below to inspect their Review Card & Peer Comparison:'
                            : 'Received submissions (click "End Submission Now" above to release and inspect peer comparisons):'),
                        el('div', { class: 'row' }, studentPills),
                    ])
                    : el('div', { class: 'empty' }, 'No student homework submissions received yet.'),
            ]);
        }

        /* ----- Student Controls: Multi-Format File Upload OR Public Git Repo URL ----- */
        let studentSection = null;
        if (!isLecturer) {
            const fileInput = el('input', { type: 'file', accept: HOMEWORK_FILE_ACCEPT, class: 'hidden' });
            const uploadFileBtn = button(
                a.viewer_has_submitted
                    ? 'Replace File (.pdf, .docx, .zip, .txt, .md, code)'
                    : 'Upload File (.pdf, .docx, .zip, .txt, .md, code)',
                { cls: 'primary sm', iconName: 'upload' },
            );
            uploadFileBtn.disabled = isReleased;

            uploadFileBtn.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', () => {
                const file = fileInput.files[0];
                if (!file) return;
                const form = new FormData();
                form.append('file', file);
                withLoading(uploadFileBtn, async () => {
                    await api.upload(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/submit`, form);
                    toast(`Homework file "${file.name}" submitted!`, 'success');
                    await loadAssignments();
                });
                fileInput.value = '';
            });

            const repoUrlInput = el('input', {
                type: 'url',
                placeholder: 'https://github.com/owner/repo (Public GitHub Repository URL)',
                style: 'min-width:260px;flex:1',
            });
            repoUrlInput.disabled = isReleased;
            const submitRepoBtn = button('Submit Public Git Repo URL', { cls: 'sm', iconName: 'sparkles' });
            submitRepoBtn.disabled = isReleased;

            submitRepoBtn.addEventListener('click', () => {
                const urlVal = repoUrlInput.value.trim();
                if (!urlVal) {
                    toast('Enter a public GitHub repository URL (e.g. https://github.com/owner/repo).', 'warning');
                    return;
                }
                withLoading(submitRepoBtn, async () => {
                    await api.post(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/submit-repo`, {
                        repo_url: urlVal,
                    });
                    repoUrlInput.value = '';
                    toast('Public Git repository submitted and analyzed!', 'success');
                    await loadAssignments();
                });
            });

            const loadMyReview = async () => {
                if (!a.viewer_has_submitted) return;
                try {
                    const revData = await api.get(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/review`);
                    mount(detailHost, renderReviewAndComparisonColumns(revData));
                } catch (err) {
                    toast(err.message, 'error');
                }
            };
            loadMyReview();

            studentSection = el('div', { class: 'stack', style: 'gap:0.65rem' }, [
                !isReleased
                    ? el('div', { class: 'row' }, [
                        uploadFileBtn,
                        fileInput,
                        repoUrlInput,
                        submitRepoBtn,
                    ])
                    : null,
                el('div', { class: 'row' }, [
                    a.viewer_has_submitted
                        ? el('span', { class: 'chip ok' }, [el('span', { html: icon('check') }), 'Homework Submitted'])
                        : (isReleased ? el('span', { class: 'chip danger' }, 'Submissions Closed') : null),
                    !isReleased
                        ? el('span', { class: 'faint' }, 'Supported: .pdf, .docx, .zip, .txt, .md, .py, .java, .c, .cpp, .h, .js, .ts, .html, .css, .json, or a Public GitHub URL.')
                        : null,
                ]),
            ]);
        }

        return el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('span', { class: `chip ${isReleased ? 'ok' : 'warn'}` }, [
                    el('span', { class: 'dot' }),
                    isReleased ? 'REVIEWS UNLOCKED' : 'SUBMISSIONS OPEN (REVIEWS GATED)',
                ]),
                el('span', { class: 'chip' }, a.lesson_tag || 'Lesson 1'),
                el('span', { class: 'faint' }, `Deadline: ${deadlineStr} · ${a.submission_count} submission(s)`),
            ]),
            el('h2', { dir: 'auto' }, a.title),
            a.description ? el('p', { class: 'muted', dir: 'auto' }, a.description) : null,
            lecturerSection,
            studentSection,
            detailHost,
        ]);
    };

    const loadAssignments = async () => {
        try {
            const { assignments } = await api.get('/api/homework/assignments');
            if (!assignments.length) {
                mount(assignmentsHost, el('div', { class: 'card empty' },
                    isLecturer
                        ? 'No homework assignments created yet. Create an assignment above!'
                        : 'No homework assignments posted yet.'));
                return;
            }
            mount(assignmentsHost, assignments.map(renderAssignmentCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    /* ----- Lecturer Assignment Creator Card ----- */
    let creatorCard = null;
    if (isLecturer) {
        const titleInput = el('input', {
            type: 'text',
            placeholder: 'e.g. Ex2: Software Engineering Architecture & Repository Submission',
            required: true,
        });
        const descInput = el('input', {
            type: 'text',
            placeholder: 'Instructions / Grading Focus (accepts PDF, DOCX, ZIP, code files, or Public GitHub Repo URL)…',
        });
        const tagSelect = el('select', {}, LESSON_TAGS.map((t) => el('option', { value: t }, t)));
        const deadlineHoursInput = el('input', {
            type: 'number',
            min: 1,
            max: 720,
            value: 48,
            required: true,
        });
        const createBtn = button('Create Homework Assignment', { cls: 'primary', type: 'submit', iconName: 'file' });

        const form = el('form', { class: 'stack' }, [
            el('div', { class: 'grid', style: 'grid-template-columns:2fr 1fr 1fr' }, [
                el('label', { class: 'field' }, ['Assignment Title', titleInput]),
                el('label', { class: 'field' }, ['Lesson Tag', tagSelect]),
                el('label', { class: 'field' }, ['Deadline (hours from now)', deadlineHoursInput]),
            ]),
            el('label', { class: 'field' }, ['Description / Rubric', descInput]),
            el('div', { class: 'row' }, [createBtn]),
        ]);

        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const hours = Number(deadlineHoursInput.value) || 24;
            const deadlineTs = Math.floor(Date.now() / 1000) + Math.round(hours * 3600);
            withLoading(createBtn, async () => {
                await api.post('/api/homework/assignments', {
                    title: titleInput.value.trim(),
                    description: descInput.value.trim(),
                    deadline_ts: deadlineTs,
                    lesson_tag: tagSelect.value,
                });
                titleInput.value = '';
                descInput.value = '';
                toast('Homework assignment created!', 'success');
                await loadAssignments();
            });
        });

        creatorCard = el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('file') }),
                el('h2', {}, 'Create Gated Homework Assignment (Files & Public Git Repo URLs)'),
            ]),
            el('p', { class: 'muted' }, 'Students can upload files (.pdf, .docx, .zip, .txt, .md, source code) or submit a Public GitHub Repository URL. AI feedback and peer comparisons remain strictly gated until the deadline passes or you click "End Submission Now".'),
            form,
        ]);
    }

    mount(container, el('div', { class: 'stack' }, [creatorCard, assignmentsHost]));
    loadAssignments();
}
