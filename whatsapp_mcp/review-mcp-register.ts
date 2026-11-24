
  // Supplemental read-only review tools. This HA packaging feature is not
  // available when the upstream MCP is built without WHATSAPP_REVIEW_MODULE.
  if (process.env["WHATSAPP_REVIEW_MODULE"] && process.env["WHATSAPP_DATA_DIR"]) {
    server.registerTool(
      "whatsapp_review_conversations",
      {
        description:
          "Review WhatsApp conversations for possible unanswered requests, commitments and dated arrangements " +
          "in one bounded, local SQLite scan. Returns compact context with message IDs for follow-up. " +
          "Heuristic flags are leads only: verify them against the full conversation and external work systems. " +
          "Reads the archive offline and never sends WhatsApp messages.",
        inputSchema: {
          days: z.number().int().min(1).max(90).default(21)
            .describe("Number of days before 'before' to inspect; default 21. Ignored when 'after' is specified."),
          after: z.number().int().nonnegative().optional()
            .describe("Optional inclusive Unix seconds UTC for oldest message; 90-day maximum window."),
          before: z.number().int().nonnegative().optional()
            .describe("Optional inclusive Unix seconds UTC for latest message; defaults to now."),
          mode: z.enum(["direct","groups","all"]).default("direct")
            .describe("Direct messages by default; groups or all are optional."),
          name_hint: z.string().min(3).max(80).optional()
            .describe("Optional name or nickname to recognise when other participants address you in a group; no name is stored in the add-on."),
          only_candidates: z.boolean().default(true)
            .describe("True returns conversations with task/reply/date indicators; false also includes ordinary conversations."),
          limit: z.number().int().min(1).max(80).default(35)
            .describe("Maximum conversations returned on one page; default 35."),
          offset: z.number().int().min(0).max(5000).default(0)
            .describe("Use 'next_offset' from the previous result to request another page."),
          context: z.number().int().min(2).max(12).default(6)
            .describe("Recent messages to return for each conversation; default 6."),
        },
        annotations: READ_ONLY_TOOL,
      },
      async (options) => {
        try {
          const modulePath = process.env["WHATSAPP_REVIEW_MODULE"];
          const storagePath = process.env["WHATSAPP_DATA_DIR"];
          if (!modulePath || !storagePath) throw new Error("Review module is not configured");
          const { reviewConversations } = await import(modulePath);
          const result = reviewConversations(storagePath, options);
          return jsonResult(result, ctx.config.maxResultChars);
        } catch (err) {
          return failedResult("whatsapp_review_conversations", err, ctx);
        }
      },
    );

    server.registerTool(
      "whatsapp_archive_filter",
      {
        description:
          "Search the read-only WhatsApp archive across resolved contact/group names and message text or transcripts, " +
          "optionally narrowed by inclusive UTC Unix-second date range and conversation type. " +
          "Works offline and never sends messages. Use when the user asks about a person/group or events in a date range, " +
          "including date-only searches without a text term. Results are paged newest first.",
        inputSchema: {
          query: z.string().max(160).default("")
            .describe("Optional case-insensitive substring in chat names, message text or transcripts. At least a query or date boundary is required."),
          scope: z.enum(["any","name","message"]).default("any")
            .describe("Search names, message contents, or both (default)."),
          after: z.number().int().min(0).max(9999999999).optional()
            .describe("Start of UTC date interval, inclusive, Unix seconds."),
          before: z.number().int().min(0).max(9999999999).optional()
            .describe("End of UTC date interval, inclusive, Unix seconds."),
          is_group: z.boolean().optional()
            .describe("True for groups only, false for direct chats only."),
          limit: z.number().int().min(1).max(60).default(30)
            .describe("Maximum results on one page, 1-60."),
          cursor: z.string().regex(/^[0-9]+$/).optional()
            .describe("Next cursor supplied by a prior result."),
        },
        annotations: READ_ONLY_TOOL,
      },
      async ({query,scope,after,before,is_group,limit,cursor}) => {
        try {
          const modulePath = process.env["WHATSAPP_ARCHIVE_SEARCH_MODULE"];
          const storagePath = process.env["WHATSAPP_DATA_DIR"];
          if(!modulePath || !storagePath) throw new Error("Archive search is not configured");
          const { ArchiveData } = await import(modulePath);
          const params = new URLSearchParams();
          for(const [key,value] of Object.entries({
            q:query,scope,after,before,isGroup:is_group,limit,cursor
          })) if(value!==undefined && value!==null) params.set(key,String(value));
          const archive = new ArchiveData(storagePath);
          try {
            return jsonResult(archive.searchArchive(params),ctx.config.maxResultChars);
          } finally {
            archive.close();
          }
        } catch(err) {
          return failedResult("whatsapp_archive_filter",err,ctx);
        }
      },
    );

    server.registerTool(
      "whatsapp_get_review_context",
      {
        description:
          "Batch-read recent conversation context for 1–20 chat IDs returned by whatsapp_review_conversations. " +
          "Includes message IDs, senders, timestamps and up to 100 messages per chat. " +
          "Local and strictly read-only. Verify completion using the context; do not infer it from heuristic flags.",
        inputSchema: {
          chats: z.array(z.string().min(1).max(300)).min(1).max(20)
            .describe("1–20 chat IDs, exactly as returned by WhatsApp tools."),
          days: z.number().int().min(1).max(90).default(21)
            .describe("Look back this many days from 'before' when no 'after' is specified."),
          after: z.number().int().nonnegative().optional(),
          before: z.number().int().nonnegative().optional(),
          limit_per_chat: z.number().int().min(1).max(100).default(40)
            .describe("Maximum recent messages per chat; default 40."),
        },
        annotations: READ_ONLY_TOOL,
      },
      async (options) => {
        try {
          const modulePath = process.env["WHATSAPP_REVIEW_MODULE"];
          const storagePath = process.env["WHATSAPP_DATA_DIR"];
          if (!modulePath || !storagePath) throw new Error("Review module is not configured");
          const { reviewContext } = await import(modulePath);
          return jsonResult(reviewContext(storagePath, options), ctx.config.maxResultChars);
        } catch (err) {
          return failedResult("whatsapp_get_review_context", err, ctx);
        }
      },
    );
  }
