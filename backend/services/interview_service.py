import os
import re
import json
import time
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

logger = logging.getLogger("interview_service")
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)

# =============================================================================
# CURATED ROLE QUESTION BANK FOR 10+ TECHNICAL IT/CS SPECIALIZATIONS
# Each role contains Fresher and Experienced sets with categories, expected concepts,
# rubrics, and time limits.
# =============================================================================

QUESTION_BANK: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "python": {
        "fresher": [
            {
                "id": "py-f-01",
                "category": "Data Structures",
                "question": "Can you explain the key differences between lists and tuples in Python, and when you would choose one over the other?",
                "expected_concepts": ["mutable", "immutable", "syntax", "brackets", "parentheses", "performance", "memory", "hashable", "dictionary key"],
                "rubric": {
                    "high": "Explains lists are mutable while tuples are immutable; mentions syntax differences, memory efficiency of tuples, and tuple usage as dictionary keys.",
                    "medium": "Mentions lists can be modified while tuples cannot, but misses memory efficiency or dictionary key suitability.",
                    "low": "Vague or incorrect distinction between mutability."
                },
                "time_limit": 120
            },
            {
                "id": "py-f-02",
                "category": "Memory & Assignment",
                "question": "How does Python handle memory management and variable assignment? What does it mean that everything in Python is an object?",
                "expected_concepts": ["reference", "object", "id", "garbage collection", "reference counting", "heap", "mutable", "immutable"],
                "rubric": {
                    "high": "Explains variables are names referencing objects in memory; describes reference counting, garbage collection and object identity.",
                    "medium": "Mentions variables point to values and automatic memory cleanup, without reference counting details.",
                    "low": "Confuses Python assignment with direct C-style value storage."
                },
                "time_limit": 120
            },
            {
                "id": "py-f-03",
                "category": "Functions & Scope",
                "question": "What are *args and **kwargs in Python functions, and how do they provide flexibility in passing arguments?",
                "expected_concepts": ["variable length", "positional arguments", "keyword arguments", "tuple", "dictionary", "unpacking", "signature"],
                "rubric": {
                    "high": "Explains *args packs positional arguments into a tuple; **kwargs packs keyword arguments into a dictionary; gives clear usage context.",
                    "medium": "Identifies args for multiple values and kwargs for key-value pairs without tuple/dict internal distinction.",
                    "low": "Unclear on how asterisks modify parameter binding."
                },
                "time_limit": 120
            },
            {
                "id": "py-f-04",
                "category": "OOP Fundamentals",
                "question": "How do inheritance and polymorphism work in Python? What is the role of the super() function?",
                "expected_concepts": ["parent class", "child class", "override", "super", "method resolution order", "mro", "code reusability", "polymorphism"],
                "rubric": {
                    "high": "Explains child class inheriting attributes/methods; method overriding for polymorphism; and super() delegating to parent or MRO.",
                    "medium": "Explains basic inheritance and calling parent methods, but omits polymorphism or MRO.",
                    "low": "Struggles to define inheritance in object-oriented terms."
                },
                "time_limit": 120
            },
            {
                "id": "py-f-05",
                "category": "Exception Handling",
                "question": "How do try, except, else, and finally blocks work together in Python error handling? When does the finally block execute?",
                "expected_concepts": ["try", "except", "else", "finally", "cleanup", "always executes", "exception", "raise"],
                "rubric": {
                    "high": "Clearly articulates that try contains risky code, except catches errors, else runs when no error occurs, and finally always executes for resource cleanup.",
                    "medium": "Explains try-except-finally correctly but misses or misidentifies the role of the else clause.",
                    "low": "Cannot clearly explain when finally executes."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "py-e-01",
                "category": "Concurrency & GIL",
                "question": "Can you explain the Python Global Interpreter Lock (GIL)? How does it affect CPU-bound versus I/O-bound tasks, and how do you bypass its limitations?",
                "expected_concepts": ["gil", "cpython", "thread", "cpu-bound", "io-bound", "multiprocessing", "asyncio", "single core", "bytecode"],
                "rubric": {
                    "high": "Explains GIL prevents multiple native threads from executing bytecodes concurrently; highlights threading/asyncio for I/O and multiprocessing/C extensions for CPU tasks.",
                    "medium": "Knows GIL restricts threading but cannot clearly separate CPU vs I/O bound impacts or architectural workarounds.",
                    "low": "Thinks Python threads automatically run concurrently on multi-core processors for CPU work."
                },
                "time_limit": 120
            },
            {
                "id": "py-e-02",
                "category": "Advanced OOP & Metaclasses",
                "question": "What are Python descriptors and metaclasses? When would you use a custom metaclass in production architecture?",
                "expected_concepts": ["__get__", "__set__", "descriptor", "metaclass", "type", "class creation", "validation", "framework", "orm"],
                "rubric": {
                    "high": "Explains descriptors manage attribute access protocol (__get__, __set__); metaclasses are classes of classes allowing class interception, validation, or ORM field mapping.",
                    "medium": "Explains descriptors as property-like constructs but has only superficial knowledge of metaclasses.",
                    "low": "Cannot explain what a metaclass does beyond standard inheritance."
                },
                "time_limit": 120
            },
            {
                "id": "py-e-03",
                "category": "Decorators & Closures",
                "question": "How do function and class decorators work internally in Python? How do you preserve wrapped function metadata using functools.wraps?",
                "expected_concepts": ["higher-order function", "closure", "wrapper", "functools.wraps", "__name__", "__doc__", "arguments", "class decorator"],
                "rubric": {
                    "high": "Explains closures capturing outer scope; higher-order functions returning wrappers; functools.wraps copying __name__, __doc__, and __annotations__.",
                    "medium": "Knows syntax @decorator but cannot articulate closure mechanism or metadata preservation.",
                    "low": "Cannot explain how a decorator takes a function as input."
                },
                "time_limit": 120
            },
            {
                "id": "py-e-04",
                "category": "Async & Event Loop",
                "question": "How does Python's asyncio event loop operate under the hood? What happens if you run blocking synchronous code inside an async coroutine?",
                "expected_concepts": ["event loop", "coroutine", "blocking", "threadpool", "run_in_executor", "cooperative", "await", "tasks"],
                "rubric": {
                    "high": "Explains cooperative multitasking via epoll/kqueue; identifies blocking code halts the entire thread; uses loop.run_in_executor to offload blocking tasks.",
                    "medium": "Recognizes async code uses await but unclear on how blocking operations freeze the event loop.",
                    "low": "Confuses asyncio with OS-level multi-threading."
                },
                "time_limit": 120
            },
            {
                "id": "py-e-05",
                "category": "High-Throughput Web APIs",
                "question": "Walk me through how you would architect a Python FastAPI application handling 5,000 requests per second with database bottlenecks.",
                "expected_concepts": ["async driver", "connection pool", "caching", "redis", "indexing", "uvicorn workers", "gunicorn", "pydantic", "n+1"],
                "rubric": {
                    "high": "Proposes async DB drivers, connection pooling, Redis caching, query indexing, avoiding N+1 queries, and multiple Uvicorn workers behind Nginx.",
                    "medium": "Mentions caching and indexes but misses connection pooling or async event-loop blocking concerns.",
                    "low": "Suggests hardware scaling without application or database architecture changes."
                },
                "time_limit": 120
            }
        ]
    },
    "java": {
        "fresher": [
            {
                "id": "java-f-01",
                "category": "OOP Core",
                "question": "Can you explain the four core pillars of Object-Oriented Programming in Java and how encapsulation is achieved?",
                "expected_concepts": ["encapsulation", "inheritance", "polymorphism", "abstraction", "private", "getter", "setter", "data hiding"],
                "rubric": {
                    "high": "Explains Encapsulation, Inheritance, Polymorphism, Abstraction clearly; details encapsulation using private fields with public getters/setters.",
                    "medium": "Names the 4 pillars but provides superficial explanations of encapsulation.",
                    "low": "Fails to name all four pillars or confuses encapsulation with inheritance."
                },
                "time_limit": 120
            },
            {
                "id": "java-f-02",
                "category": "JVM Architecture",
                "question": "What is the difference between JDK, JRE, and JVM? How does Java achieve platform independence with bytecode?",
                "expected_concepts": ["jdk", "jre", "jvm", "bytecode", "platform independent", "jit", "classloader", "javac"],
                "rubric": {
                    "high": "Explains JDK contains development tools; JRE provides runtime libraries; JVM executes bytecode; 'write once, run anywhere' via JVM implementations.",
                    "medium": "Defines acronyms accurately but misses JIT compiler or classloader details.",
                    "low": "Confuses bytecode with machine code."
                },
                "time_limit": 120
            },
            {
                "id": "java-f-03",
                "category": "Collections Framework",
                "question": "What are the key differences between an ArrayList and a LinkedList in Java? In what scenarios would you choose each?",
                "expected_concepts": ["arraylist", "linkedlist", "dynamic array", "node", "pointers", "o(1)", "o(n)", "random access", "insertion"],
                "rubric": {
                    "high": "Contrasts ArrayList (contiguous memory, O(1) random access) with LinkedList (doubly linked nodes, O(1) head/tail insertion, O(n) search).",
                    "medium": "Knows ArrayList is faster for retrieval and LinkedList for insertion, without underlying data structure specifics.",
                    "low": "Unaware of array versus node-based memory layout."
                },
                "time_limit": 120
            },
            {
                "id": "java-f-04",
                "category": "Exceptions & Strings",
                "question": "Explain the difference between checked and unchecked exceptions in Java, and why Strings are immutable.",
                "expected_concepts": ["checked", "unchecked", "runtimeexception", "immutable", "string pool", "thread safe", "try catch"],
                "rubric": {
                    "high": "Explains checked exceptions verified at compile-time (IOException); unchecked subclasses of RuntimeException; String immutability enables String pool and thread safety.",
                    "medium": "Differentiates compile-time vs runtime exceptions, but cannot fully explain the reasons for String immutability.",
                    "low": "Cannot differentiate checked vs unchecked exception hierarchy."
                },
                "time_limit": 120
            },
            {
                "id": "java-f-05",
                "category": "Spring Boot Basics",
                "question": "What is Dependency Injection in Spring Boot, and what is the difference between @Component, @Service, and @Repository?",
                "expected_concepts": ["dependency injection", "ioc container", "component", "service", "repository", "beans", "autowired"],
                "rubric": {
                    "high": "Explains IoC container injecting dependencies; @Component generic stereotype; @Service holding business logic; @Repository handling DAO with exception translation.",
                    "medium": "Explains Dependency Injection basics but cannot clearly separate the responsibilities of the stereotype annotations.",
                    "low": "Does not know how Spring manages beans."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "java-e-01",
                "category": "JVM Memory & Garbage Collection",
                "question": "Explain JVM memory structure (Heap, Stack, Metaspace) and how modern generational garbage collectors like G1GC or ZGC manage memory.",
                "expected_concepts": ["heap", "stack", "metaspace", "young generation", "old generation", "g1gc", "stop-the-world", "eden", "zgc"],
                "rubric": {
                    "high": "Details young (Eden, Survivor) vs old generation; stack frames for local variables; Metaspace for class metadata; G1GC regional partitioning and low-pause GC.",
                    "medium": "Mentions Heap and Stack, but lacks generational collector mechanics or Metaspace knowledge.",
                    "low": "Believes all objects and primitives are stored in a single memory pool."
                },
                "time_limit": 120
            },
            {
                "id": "java-e-02",
                "category": "Concurrency & Multithreading",
                "question": "How do you achieve thread safety in Java? Compare synchronized blocks, volatile variables, ReentrantLock, and Atomic classes.",
                "expected_concepts": ["synchronized", "volatile", "visibility", "reentrantlock", "atomic", "cas", "thread safe", "deadlock"],
                "rubric": {
                    "high": "Explains volatile guarantees visibility without atomicity; synchronized ensures mutual exclusion; ReentrantLock provides fairness and timeouts; Atomic classes use hardware CAS.",
                    "medium": "Knows synchronized prevents multiple threads entering, but struggles to articulate volatile visibility vs atomicity.",
                    "low": "Cannot explain race conditions or thread safety mechanisms."
                },
                "time_limit": 120
            },
            {
                "id": "java-e-03",
                "category": "Spring Architecture & Transactions",
                "question": "How does Spring manage transaction propagation (e.g. REQUIRED vs REQUIRES_NEW) using the @Transactional annotation? How do AOP proxies handle internal calls?",
                "expected_concepts": ["transactional", "aop proxy", "propagation", "required", "requires_new", "rollback", "self-invocation"],
                "rubric": {
                    "high": "Explains Spring AOP proxy interception; self-invocation bypassing proxy; details REQUIRED joining existing vs REQUIRES_NEW suspending existing transaction.",
                    "medium": "Explains @Transactional rollback basics, but misses AOP proxy bypass on self-invocation.",
                    "low": "Unaware of transaction propagation levels."
                },
                "time_limit": 120
            },
            {
                "id": "java-e-04",
                "category": "Microservices Resiliency",
                "question": "How do you handle distributed transactions and service resiliency in Spring Boot microservices? Mention patterns like Saga, Circuit Breaker, or Kafka messaging.",
                "expected_concepts": ["saga", "circuit breaker", "resilience4j", "kafka", "eventual consistency", "idempotency", "outbox"],
                "rubric": {
                    "high": "Explains Saga pattern (orchestration vs choreography) with compensating transactions; Resilience4j circuit breakers; transactional outbox pattern for reliable event publishing.",
                    "medium": "Names circuit breakers and Kafka, but cannot explain compensating transactions or eventual consistency.",
                    "low": "Proposes two-phase commit across microservices without addressing blocking bottlenecks."
                },
                "time_limit": 120
            },
            {
                "id": "java-e-05",
                "category": "Performance Diagnostics",
                "question": "Walk me through how you diagnose and resolve a high CPU spike or memory leak in a production Java application using thread and heap dumps.",
                "expected_concepts": ["thread dump", "heap dump", "jcmd", "jvisualvm", "profiler", "memory leak", "gc pause", "top"],
                "rubric": {
                    "high": "Correlates high-CPU OS threads (top -H) to Java thread dump hex IDs; inspects RUNNABLE loops/locks; analyzes heap dumps (MAT/VisualVM) for retained object memory leaks.",
                    "medium": "Mentions taking thread and heap dumps, but lacks specific diagnostic workflow or tools.",
                    "low": "Suggests restarting the server without collecting diagnostic artifacts."
                },
                "time_limit": 120
            }
        ]
    },
    "fullstack": {
        "fresher": [
            {
                "id": "fs-f-01",
                "category": "Web Architecture",
                "question": "Walk me through what happens from the moment a user types a URL and hits enter in the browser until the web page is fully rendered.",
                "expected_concepts": ["dns", "tcp", "http", "tls", "html", "dom", "cssom", "render tree", "backend api"],
                "rubric": {
                    "high": "Traces DNS resolution, TCP handshake, TLS negotiation, HTTP request/response, DOM/CSSOM parsing, render tree construction, and script execution.",
                    "medium": "Covers browser request and server response, but omits DNS lookup, TLS handshake, or browser rendering pipeline.",
                    "low": "Only mentions the server sends an HTML file."
                },
                "time_limit": 120
            },
            {
                "id": "fs-f-02",
                "category": "Frontend State & React",
                "question": "How does React's Virtual DOM work, and how do useState and useEffect hooks manage component lifecycle and state updates?",
                "expected_concepts": ["virtual dom", "diffing", "reconciliation", "usestate", "useeffect", "state", "props", "re-render"],
                "rubric": {
                    "high": "Explains in-memory Virtual DOM representation, diffing algorithm, batch DOM updates, and how useState triggers reconciliation while useEffect manages side effects.",
                    "medium": "Knows Virtual DOM is faster than real DOM and defines basic useState/useEffect usage.",
                    "low": "Thinks Virtual DOM completely replaces HTML."
                },
                "time_limit": 120
            },
            {
                "id": "fs-f-03",
                "category": "REST API Design",
                "question": "What makes an API RESTful? Explain the role of HTTP methods and common status codes like 200, 201, 400, 401, 404, and 500.",
                "expected_concepts": ["rest", "stateless", "http methods", "get", "post", "put", "delete", "status codes", "idempotent", "json"],
                "rubric": {
                    "high": "Articulates REST constraints (statelessness, resource-based URIs); correctly defines HTTP verbs and maps status codes accurately.",
                    "medium": "Defines common status codes and methods, but cannot explain idempotency or statelessness.",
                    "low": "Confuses GET and POST methods or misidentifies status codes."
                },
                "time_limit": 120
            },
            {
                "id": "fs-f-04",
                "category": "Authentication",
                "question": "How does token-based authentication with JWT work between a frontend application and a backend server? Where should tokens be stored securely?",
                "expected_concepts": ["jwt", "token", "header", "bearer", "http-only cookie", "localstorage", "claims", "signature", "xss"],
                "rubric": {
                    "high": "Explains JWT structure (header, payload, signature); transmission in Authorization Bearer header; notes httpOnly cookies protect against XSS compared to localStorage.",
                    "medium": "Explains login issuing a token sent with subsequent requests, without security trade-offs of storage options.",
                    "low": "Unclear on token verification or signature checking."
                },
                "time_limit": 120
            },
            {
                "id": "fs-f-05",
                "category": "Database Selection",
                "question": "Compare relational SQL databases (PostgreSQL/MySQL) with NoSQL databases (MongoDB). When would you select each for a full-stack project?",
                "expected_concepts": ["relational", "sql", "nosql", "schema", "acid", "document", "mongodb", "postgresql", "joins"],
                "rubric": {
                    "high": "Contrasts structured tables, ACID compliance, and JOINs in SQL with flexible schema, JSON documents, and horizontal sharding in NoSQL.",
                    "medium": "Knows SQL uses tables and NoSQL uses documents, but cannot explain transactional ACID guarantees or scaling differences.",
                    "low": "Believes one database type is universally superior."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "fs-e-01",
                "category": "Full Stack System Architecture",
                "question": "How would you design a scalable full-stack real-time collaborative application (like Google Docs or Figma)? Describe the frontend, WebSockets, conflict resolution, and database layers.",
                "expected_concepts": ["websocket", "operational transformation", "crdt", "redis", "pubsub", "concurrency", "optimistic updates"],
                "rubric": {
                    "high": "Architects bidirectional WebSockets with Redis Pub/Sub; CRDT or Operational Transformation for concurrent conflict resolution; optimistic frontend UI updates.",
                    "medium": "Proposes WebSockets and a database, but lacks conflict resolution algorithms (CRDT/OT) or horizontal socket scaling.",
                    "low": "Suggests polling the server every second for collaborative changes."
                },
                "time_limit": 120
            },
            {
                "id": "fs-e-02",
                "category": "Security & Vulnerabilities",
                "question": "Explain how you protect a full-stack web application against Cross-Site Scripting (XSS), Cross-Site Request Forgery (CSRF), and SQL Injection.",
                "expected_concepts": ["xss", "csrf", "sql injection", "cors", "csp", "sanitize", "prepared statements", "same-site cookies"],
                "rubric": {
                    "high": "Details CSP headers, sanitization/escaping for XSS; SameSite cookies and anti-CSRF tokens for CSRF; parameterized queries and ORM validation for SQL injection.",
                    "medium": "Defines each attack type correctly, but gives superficial mitigation techniques.",
                    "low": "Confuses XSS with CSRF."
                },
                "time_limit": 120
            },
            {
                "id": "fs-e-03",
                "category": "Caching & Multi-Tier Performance",
                "question": "Describe your caching strategy across the full stack: browser cache, CDN, reverse proxy, Redis application cache, and database query caching.",
                "expected_concepts": ["cdn", "redis", "cache invalidation", "cache-control", "etag", "stale-while-revalidate", "write-through"],
                "rubric": {
                    "high": "Explains Cache-Control/ETags at edge; CDN caching for static assets; Redis for active user sessions/hot data; cache invalidation patterns (write-through/cache-aside).",
                    "medium": "Knows Redis and browser caching, but cannot articulate cache invalidation strategies or edge CDN caching.",
                    "low": "Cannot explain how a cache prevents database overload."
                },
                "time_limit": 120
            },
            {
                "id": "fs-e-04",
                "category": "Microservices vs Monolith",
                "question": "What are the architectural trade-offs between monolithic full-stack applications and microservices/microfrontends? How do you manage deployment and shared state?",
                "expected_concepts": ["microservices", "microfrontends", "module federation", "api gateway", "coupling", "deployments", "ci/cd"],
                "rubric": {
                    "high": "Evaluates operational complexity, network latency, distributed tracing vs monolith ease of testing; Module Federation for microfrontends; API Gateway routing.",
                    "medium": "Lists pros and cons of microservices, but omits microfrontend state management or operational overhead.",
                    "low": "Asserts microservices should always be used regardless of team size."
                },
                "time_limit": 120
            },
            {
                "id": "fs-e-05",
                "category": "Observability & Zero-Downtime Deployments",
                "question": "How do you set up automated end-to-end testing, zero-downtime deployment (blue-green or rolling), and centralized logging across frontend and backend services?",
                "expected_concepts": ["ci/cd", "docker", "blue-green", "canary", "e2e", "prometheus", "grafana", "sentry", "logs"],
                "rubric": {
                    "high": "Explains Blue-Green/Canary traffic routing; schema migrations without breaking old versions; Sentry for frontend errors; centralized tracing and Prometheus/Grafana.",
                    "medium": "Describes CI/CD pipelines, but lacks zero-downtime database migration strategies or observability correlation.",
                    "low": "Suggests taking down the production server during off-peak hours for deployment."
                },
                "time_limit": 120
            }
        ]
    },
    "frontend": {
        "fresher": [
            {
                "id": "fe-f-01",
                "category": "Core Web & DOM",
                "question": "Explain the CSS Box Model (margin, border, padding, content) and the difference between Flexbox and CSS Grid layout systems.",
                "expected_concepts": ["box model", "margin", "padding", "border", "content", "box-sizing", "flexbox", "grid", "responsive"],
                "rubric": {
                    "high": "Details all box model components and box-sizing; explains Flexbox is 1-dimensional (row or column) while CSS Grid is 2-dimensional (rows and columns simultaneously).",
                    "medium": "Explains box model but cannot articulate when Grid is preferred over Flexbox.",
                    "low": "Confuses padding with margin or Flexbox with floats."
                },
                "time_limit": 120
            },
            {
                "id": "fe-f-02",
                "category": "JavaScript Event Loop",
                "question": "What is the JavaScript Event Loop, and how do the call stack, microtask queue (Promises), and macrotask queue (setTimeout) interact?",
                "expected_concepts": ["event loop", "call stack", "microtask", "macrotask", "promises", "settimeout", "asynchronous"],
                "rubric": {
                    "high": "Explains single-threaded call stack execution; microtasks (Promise.then, queueMicrotask) draining completely before macrotasks (setTimeout, setInterval).",
                    "medium": "Knows setTimeout is asynchronous and runs after synchronous code, but misses microtask vs macrotask priority.",
                    "low": "Believes JavaScript executes multi-threaded code in browser tabs natively."
                },
                "time_limit": 120
            },
            {
                "id": "fe-f-03",
                "category": "React Fundamentals",
                "question": "Explain React props vs state, controlled vs uncontrolled inputs, and why unique keys are mandatory when rendering lists.",
                "expected_concepts": ["props", "state", "controlled", "keys", "reconciliation", "forms", "immutable"],
                "rubric": {
                    "high": "Contrasts immutable props passed from parent with internal state; explains controlled inputs bound to state; notes keys preserve identity during reconciliation.",
                    "medium": "Differentiates props and state, but cannot explain why list indices as keys cause subtle UI bugs.",
                    "low": "Confuses props with state."
                },
                "time_limit": 120
            },
            {
                "id": "fe-f-04",
                "category": "Web Performance",
                "question": "What are Core Web Vitals (LCP, INP, CLS), and what frontend techniques do you use to optimize asset loading and page speed?",
                "expected_concepts": ["lcp", "cls", "inp", "lazy loading", "code splitting", "minification", "images", "caching"],
                "rubric": {
                    "high": "Defines Largest Contentful Paint, Interaction to Next Paint, and Cumulative Layout Shift; details code-splitting, WebP/AVIF images, lazy loading, and dimension reservation.",
                    "medium": "Mentions image compression and minification, but cannot define the specific Core Web Vital metrics.",
                    "low": "Unaware of modern web performance measurement tools or metrics."
                },
                "time_limit": 120
            },
            {
                "id": "fe-f-05",
                "category": "Accessibility & Responsive Design",
                "question": "How do you ensure web accessibility (a11y) and mobile responsiveness using semantic HTML, ARIA tags, and media queries?",
                "expected_concepts": ["accessibility", "aria", "semantic html", "media queries", "screen readers", "contrast", "keyboard navigation"],
                "rubric": {
                    "high": "Emphasizes semantic HTML first; ARIA attributes for dynamic widgets; keyboard tab navigation; color contrast ratios; and mobile-first CSS media queries.",
                    "medium": "Explains media queries for mobile, but has limited familiarity with ARIA or screen reader accessibility.",
                    "low": "Views accessibility as purely an optional visual consideration."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "fe-e-01",
                "category": "Advanced State Architecture",
                "question": "Compare modern frontend state management patterns: Context API, Redux Toolkit, Zustand, and React Query / TanStack Query for server state caching.",
                "expected_concepts": ["server state", "client state", "zustand", "redux", "react query", "re-renders", "memoization"],
                "rubric": {
                    "high": "Distinguishes client state (UI state) from server state (cached API data, background refetching); explains Context re-render pitfalls and Zustand/Redux selector subscriptions.",
                    "medium": "Compares Redux and Context API, but misses the distinction between server-state libraries (TanStack Query) and global store state.",
                    "low": "Recommends putting all API data in global Context without memoization."
                },
                "time_limit": 120
            },
            {
                "id": "fe-e-02",
                "category": "SSR, SSG & Hydration",
                "question": "Explain Server-Side Rendering (SSR), Static Site Generation (SSG), and Progressive Hydration in Next.js or modern frontend meta-frameworks.",
                "expected_concepts": ["ssr", "ssg", "hydration", "next.js", "seo", "server components", "bundle size", "ttfb"],
                "rubric": {
                    "high": "Contrasts build-time SSG with runtime SSR; explains hydration attaching DOM listeners to server HTML; details React Server Components reducing client bundle size.",
                    "medium": "Defines SSR and SSG for SEO benefits, but cannot articulate hydration or Server Components.",
                    "low": "Confuses SSR with standard Client-Side SPA rendering."
                },
                "time_limit": 120
            },
            {
                "id": "fe-e-03",
                "category": "Bundler Architecture & Vite",
                "question": "How do modern bundlers like Vite (native ES modules + esbuild/Rollup) differ from traditional Webpack? How do you implement dynamic import code-splitting?",
                "expected_concepts": ["vite", "es modules", "code splitting", "tree shaking", "dynamic import", "rollup", "chunks"],
                "rubric": {
                    "high": "Explains Vite serving unbundled ESM in dev using browser module loading and esbuild; contrasts with Webpack full bundle builds; implements React.lazy dynamic imports.",
                    "medium": "Knows Vite is faster than Webpack, but cannot explain native ES modules in browser or Rollup production bundling.",
                    "low": "Unaware of what a frontend bundler does."
                },
                "time_limit": 120
            },
            {
                "id": "fe-e-04",
                "category": "Large-Scale UI Virtualization",
                "question": "How do you render a virtualized data table with 50,000 rows while keeping frame rate at 60 FPS without DOM thrashing?",
                "expected_concepts": ["virtualization", "windowing", "react-window", "requestanimationframe", "dom thrashing", "re-renders"],
                "rubric": {
                    "high": "Explains windowing (react-window/TanStack Virtual) only mounting visible row elements in DOM; calculating scroll offsets; debouncing scroll events to maintain 60 FPS.",
                    "medium": "Suggests pagination or infinite scroll, but misses virtual windowing technique for maintaining continuous scroll.",
                    "low": "Suggests rendering all 50,000 DOM elements and using CSS display:none."
                },
                "time_limit": 120
            },
            {
                "id": "fe-e-05",
                "category": "Microfrontends Architecture",
                "question": "How do you architect a large enterprise microfrontend application using Webpack Module Federation or single-spa while preventing dependency duplication?",
                "expected_concepts": ["module federation", "microfrontends", "shared dependencies", "runtime integration", "isolation"],
                "rubric": {
                    "high": "Details Module Federation remotes and host; configuring shared singletons (e.g. React, design system); version negotiation; CSS scoping and error boundaries.",
                    "medium": "Understands breaking UI into independent apps, but lacks knowledge of Module Federation shared dependency handling.",
                    "low": "Suggests using iframes for all microfrontend communication."
                },
                "time_limit": 120
            }
        ]
    },
    "backend": {
        "fresher": [
            {
                "id": "be-f-01",
                "category": "API Protocols",
                "question": "Explain the differences between REST, GraphQL, and gRPC. When would you choose gRPC over REST for microservices?",
                "expected_concepts": ["rest", "graphql", "grpc", "protobuf", "http/2", "payload", "microservices", "streaming"],
                "rubric": {
                    "high": "Contrasts REST resource endpoints with GraphQL client queries and gRPC binary Protocol Buffers over HTTP/2; highlights gRPC for low-latency inter-service communication.",
                    "medium": "Knows REST uses JSON and GraphQL lets clients pick fields, but misses gRPC Protobuf or HTTP/2 transport benefits.",
                    "low": "Cannot differentiate API protocols."
                },
                "time_limit": 120
            },
            {
                "id": "be-f-02",
                "category": "Database Indexing",
                "question": "How do B-tree database indexes work? Why does adding an index speed up SELECT queries but slow down INSERTs and UPDATEs?",
                "expected_concepts": ["b-tree", "index", "lookup", "binary search", "insert overhead", "write amplification", "select speed"],
                "rubric": {
                    "high": "Explains balanced B-tree O(log n) search traversal; write overhead from maintaining tree balance and updating index pages on writes.",
                    "medium": "Knows indexes act like book indexes, but cannot explain write overhead on disk pages.",
                    "low": "Believes indexes speed up all database operations including writes."
                },
                "time_limit": 120
            },
            {
                "id": "be-f-03",
                "category": "Authentication & Sessions",
                "question": "What is the difference between stateful session-based authentication (Redis/cookies) and stateless JWT authentication?",
                "expected_concepts": ["session", "jwt", "stateless", "stateful", "redis", "cookies", "invalidation", "scaling"],
                "rubric": {
                    "high": "Contrasts server-stored sessions (easy instant revocation, database lookup) with self-contained JWTs (horizontal scalability, difficult immediate invalidation).",
                    "medium": "Defines sessions and tokens, but struggles with the trade-offs regarding token invalidation.",
                    "low": "Confuses authentication with authorization."
                },
                "time_limit": 120
            },
            {
                "id": "be-f-04",
                "category": "Concurrency & Asynchrony",
                "question": "What is the difference between synchronous, multithreaded, and asynchronous non-blocking event-driven backend architectures?",
                "expected_concepts": ["sync", "async", "threads", "non-blocking", "event loop", "io-bound", "concurrency"],
                "rubric": {
                    "high": "Contrasts thread-per-request blocking (memory overhead per thread) with event-driven non-blocking I/O (single thread multiplexing thousands of sockets via epoll).",
                    "medium": "Explains synchronous waits while asynchronous continues, but lacks operating system thread vs event loop distinction.",
                    "low": "Confuses asynchronous execution with running multiple CPU cores."
                },
                "time_limit": 120
            },
            {
                "id": "be-f-05",
                "category": "Transactions & ACID",
                "question": "Explain the ACID properties of relational databases and what problems can occur if transaction isolation levels are too low.",
                "expected_concepts": ["atomicity", "consistency", "isolation", "durability", "dirty read", "phantom read", "transactions"],
                "rubric": {
                    "high": "Defines Atomicity, Consistency, Isolation, Durability; explains anomalies (dirty reads, non-repeatable reads, phantom reads) across Read Committed to Serializable.",
                    "medium": "Defines ACID terms accurately, but cannot explain isolation anomalies like dirty reads.",
                    "low": "Cannot name all four ACID properties."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "be-e-01",
                "category": "Distributed Systems & CAP",
                "question": "Explain the CAP theorem and PACELC theorem. How do database partitioning, sharding, and consistent hashing operate in distributed storage?",
                "expected_concepts": ["cap theorem", "consistency", "availability", "partition tolerance", "consistent hashing", "sharding"],
                "rubric": {
                    "high": "Explains trade-off between Consistency and Availability during network partitions; PACELC latency vs consistency in normal state; consistent hashing ring for minimal key migration.",
                    "medium": "Explains CAP theorem basics, but cannot explain consistent hashing or PACELC extensions.",
                    "low": "Believes a distributed system can achieve 100% Consistency and Availability simultaneously during partitions."
                },
                "time_limit": 120
            },
            {
                "id": "be-e-02",
                "category": "Asynchronous Message Queues",
                "question": "How do you design an asynchronous background processing pipeline using message brokers like Kafka or RabbitMQ with at-least-once delivery, idempotency, and dead-letter queues?",
                "expected_concepts": ["kafka", "rabbitmq", "idempotency", "dead letter queue", "consumer group", "acknowledgment", "offsets"],
                "rubric": {
                    "high": "Details message acknowledgment, consumer offset commits, unique idempotency keys in database/Redis, retry exponential backoff, and DLQ routing.",
                    "medium": "Mentions using RabbitMQ with retries, but misses idempotency deduplication or DLQ architecture.",
                    "low": "Suggests processing background jobs directly inside HTTP request handlers."
                },
                "time_limit": 120
            },
            {
                "id": "be-e-03",
                "category": "Distributed Rate Limiting",
                "question": "How do you design a distributed rate limiter supporting millions of requests per minute? Compare token bucket, leaky bucket, and Redis sliding-window algorithms.",
                "expected_concepts": ["rate limiter", "token bucket", "leaky bucket", "sliding window", "redis", "lua script", "concurrency"],
                "rubric": {
                    "high": "Contrasts algorithms; implements Redis sorted set (ZSET) or token bucket with atomic Lua scripts to avoid race conditions across multiple application servers.",
                    "medium": "Explains rate limiting concepts, but proposes in-memory counters that fail across distributed multi-server clusters.",
                    "low": "Cannot describe how a rate limiter algorithm calculates limits."
                },
                "time_limit": 120
            },
            {
                "id": "be-e-04",
                "category": "Database Scalability & Sharding",
                "question": "When your primary SQL database exceeds storage or IOPS limits, what is your strategy for read-replicas, vertical partitioning, and horizontal sharding?",
                "expected_concepts": ["read replica", "sharding", "shard key", "connection pooling", "replication lag", "failover"],
                "rubric": {
                    "high": "Details read-replica routing for read-heavy loads; shard key selection to avoid cross-shard JOINs; replication lag handling; and database connection pooling (PgBouncer).",
                    "medium": "Suggests read-replicas, but lacks sharding strategies or how to handle replication lag.",
                    "low": "Suggests continually upgrading server RAM without architectural partitioning."
                },
                "time_limit": 120
            },
            {
                "id": "be-e-05",
                "category": "API Security & Zero Trust",
                "question": "How do you secure inter-service communication in a microservices cluster using mTLS, API gateways, OAuth2 scopes, and secrets rotation?",
                "expected_concepts": ["mtls", "api gateway", "oauth2", "jwt", "secrets", "vault", "zero trust", "encryption in transit"],
                "rubric": {
                    "high": "Architects Mutual TLS for bidirectional certificate verification; API gateway edge token validation; fine-grained service scopes; and HashiCorp Vault dynamic secret leases.",
                    "medium": "Mentions API tokens and HTTPS, but lacks mTLS or automated secrets rotation.",
                    "low": "Suggests hardcoding database passwords in configuration files."
                },
                "time_limit": 120
            }
        ]
    },
    "data_analyst": {
        "fresher": [
            {
                "id": "da-f-01",
                "category": "SQL & Aggregations",
                "question": "What is the difference between WHERE and HAVING in SQL? Explain how window functions like ROW_NUMBER(), RANK(), and DENSE_RANK() work.",
                "expected_concepts": ["where", "having", "group by", "window functions", "row_number", "rank", "dense_rank", "partition by"],
                "rubric": {
                    "high": "Explains WHERE filters rows before aggregation; HAVING filters aggregated groups; details PARTITION BY and how ROW_NUMBER, RANK (skips), and DENSE_RANK handle ties.",
                    "medium": "Knows WHERE vs HAVING with GROUP BY, but struggles to distinguish RANK vs DENSE_RANK.",
                    "low": "Confuses WHERE with HAVING."
                },
                "time_limit": 120
            },
            {
                "id": "da-f-02",
                "category": "Data Wrangling with Pandas",
                "question": "How do you handle missing values, duplicates, and merge datasets in Pandas using merge(), concat(), and groupby()?",
                "expected_concepts": ["pandas", "merge", "concat", "groupby", "fillna", "dropna", "data cleaning", "dataframe"],
                "rubric": {
                    "high": "Details fillna/dropna strategies (mean, median, forward fill); inner vs outer merges; concatenating along axes; and aggregation functions in groupby.",
                    "medium": "Explains basic dropna and merge syntax, but lacks imputation considerations.",
                    "low": "Unfamiliar with Pandas DataFrame manipulation functions."
                },
                "time_limit": 120
            },
            {
                "id": "da-f-03",
                "category": "Statistical Fundamentals",
                "question": "What is the difference between correlation and causation? Explain p-value and how you interpret a 95% confidence interval.",
                "expected_concepts": ["correlation", "causation", "p-value", "significance", "confidence interval", "hypothesis testing"],
                "rubric": {
                    "high": "Articulates correlation does not imply causation; p-value as probability of observing data under null hypothesis; 95% CI containing true parameter in 95% of hypothetical samples.",
                    "medium": "Explains correlation vs causation with examples, but gives an inaccurate definition of p-value.",
                    "low": "Thinks a high correlation proves direct causation."
                },
                "time_limit": 120
            },
            {
                "id": "da-f-04",
                "category": "Data Visualization & Storytelling",
                "question": "How do you choose between bar charts, line charts, scatter plots, and heatmaps when presenting key business metrics to non-technical stakeholders?",
                "expected_concepts": ["visualization", "bar chart", "line chart", "scatter plot", "heatmap", "stakeholders", "insights", "trends"],
                "rubric": {
                    "high": "Maps categorical comparisons to bar charts, time trends to line charts, relationships/correlations to scatter plots, and 2D matrix intensity to heatmaps with focus on actionable insights.",
                    "medium": "Defines chart types accurately, but lacks stakeholder presentation communication considerations.",
                    "low": "Cannot match chart types to data dimensionality."
                },
                "time_limit": 120
            },
            {
                "id": "da-f-05",
                "category": "Business Analytics Scenario",
                "question": "If monthly user retention dropped by 15%, what step-by-step diagnostic process would you use to pinpoint the root cause?",
                "expected_concepts": ["cohort analysis", "segmentation", "funnel", "user behavior", "feature release", "retention", "metrics"],
                "rubric": {
                    "high": "Proposes cohort analysis; segmenting by platform, geography, and acquisition channel; auditing user funnels and recent releases; verifying telemetry instrumentation.",
                    "medium": "Suggests querying active users, but lacks cohort analysis or funnel segmentation methodologies.",
                    "low": "Guesses reasons without data-driven diagnostic steps."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "da-e-01",
                "category": "A/B Testing & Experimentation",
                "question": "How do you calculate sample size and statistical power for an A/B test? How do you guard against Type I and Type II errors and sample ratio mismatch?",
                "expected_concepts": ["sample size", "statistical power", "type 1 error", "type 2 error", "p-hacking", "sample ratio mismatch", "a/b test"],
                "rubric": {
                    "high": "Explains power analysis (alpha, beta, MDE, baseline conversion); guards against early stopping (p-hacking); verifies Chi-square test on sample ratio mismatch (SRM).",
                    "medium": "Explains A/B testing and confidence levels, but cannot explain statistical power or Sample Ratio Mismatch.",
                    "low": "Suggests ending an experiment as soon as p < 0.05 is observed on day one."
                },
                "time_limit": 120
            },
            {
                "id": "da-e-02",
                "category": "Data Warehousing & Modeling",
                "question": "Explain dimensional modeling: Star Schema versus Snowflake Schema. How do facts and dimensions differ, and what are slowly changing dimensions (SCD)?",
                "expected_concepts": ["star schema", "snowflake schema", "fact table", "dimension", "scd", "data warehouse", "snowflake", "bigquery"],
                "rubric": {
                    "high": "Contrasts de-normalized Star Schema query speed with normalized Snowflake Schema storage; explains fact metrics vs descriptive dimensions; SCD Type 1 vs Type 2 history tracking.",
                    "medium": "Defines facts and dimensions, but cannot explain slowly changing dimension handling.",
                    "low": "Confuses transactional OLTP schemas with analytical data warehouse dimensional schemas."
                },
                "time_limit": 120
            },
            {
                "id": "da-e-03",
                "category": "Advanced Analytics & ML",
                "question": "How do you build a customer churn prediction or lifetime value (LTV) model? Explain feature engineering, survival analysis, and model interpretation (SHAP values).",
                "expected_concepts": ["churn", "ltv", "feature engineering", "shap", "xgboost", "survival analysis", "interpretability"],
                "rubric": {
                    "high": "Details behavioral recency/frequency features; survival analysis for time-to-churn; XGBoost classification; and SHAP values for explaining individual customer risk drivers.",
                    "medium": "Explains basic churn classification with Logistic Regression, without feature engineering depth or SHAP explainability.",
                    "low": "Treats churn purely as an unmeasurable descriptive statistic."
                },
                "time_limit": 120
            },
            {
                "id": "da-e-04",
                "category": "Big Data & Distributed SQL",
                "question": "How do PySpark and distributed query engines manage data partitions, shuffles, and broadcast joins when querying terabyte-scale datasets?",
                "expected_concepts": ["pyspark", "shuffle", "broadcast join", "partitions", "skew", "memory management", "lazy evaluation"],
                "rubric": {
                    "high": "Explains partition distribution; costly shuffle operations across network; broadcast joins replicating small lookup tables to avoid shuffles; managing data skew with salting.",
                    "medium": "Knows Spark runs distributed queries, but lacks understanding of shuffles or broadcast join optimizations.",
                    "low": "Unaware of memory limitations when handling large datasets."
                },
                "time_limit": 120
            },
            {
                "id": "da-e-05",
                "category": "Data Quality & Governance",
                "question": "How do you enforce automated data quality checks, data lineage, and anomaly detection in production pipelines using dbt or Great Expectations?",
                "expected_concepts": ["dbt", "great expectations", "data quality", "lineage", "freshness", "anomalies", "testing"],
                "rubric": {
                    "high": "Details automated assertion testing (unique, not_null, referential integrity); schema contracts; lineage DAGs in dbt; and alerting on data freshness anomalies.",
                    "medium": "Mentions writing manual SQL check queries, but lacks automated framework integration.",
                    "low": "Relies exclusively on end-users to notice corrupted reports."
                },
                "time_limit": 120
            }
        ]
    },
    "ai_ml": {
        "fresher": [
            {
                "id": "aiml-f-01",
                "category": "Machine Learning Fundamentals",
                "question": "Explain the bias-variance tradeoff in machine learning. How do overfitting and underfitting manifest, and what techniques prevent overfitting?",
                "expected_concepts": ["bias", "variance", "overfitting", "underfitting", "regularization", "cross-validation", "dropout"],
                "rubric": {
                    "high": "Contrasts high bias (underfitting, model too simple) with high variance (overfitting, memorizing noise); details L1/L2 regularization, cross-validation, and dropout.",
                    "medium": "Defines overfitting and underfitting accurately, but misses mathematical bias-variance decomposition.",
                    "low": "Confuses bias with demographic fairness and variance with dataset size."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-f-02",
                "category": "Evaluation Metrics",
                "question": "When evaluating an imbalanced classification model (e.g. fraud detection), why is accuracy misleading? Explain Precision, Recall, F1-Score, and ROC-AUC.",
                "expected_concepts": ["precision", "recall", "f1-score", "roc-auc", "imbalanced", "false positive", "false negative", "confusion matrix"],
                "rubric": {
                    "high": "Explains accuracy paradox (predicting majority class yields high accuracy but zero utility); details Precision (TP/(TP+FP)), Recall (TP/(TP+FN)), harmonic mean F1, and ROC-AUC.",
                    "medium": "Defines Precision and Recall correctly, but cannot explain ROC-AUC curve interpretation.",
                    "low": "Suggests accuracy is sufficient for evaluating 99% imbalanced fraud data."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-f-03",
                "category": "Feature Engineering",
                "question": "What methods do you use for handling missing data, outlier detection, and scaling continuous features (StandardScaler vs MinMaxScaler)?",
                "expected_concepts": ["imputation", "outliers", "standardscaler", "minmaxscaler", "normalization", "one-hot encoding"],
                "rubric": {
                    "high": "Details mean/median/KNN imputation; IQR and Z-score outlier detection; contrasts StandardScaler (z-score, robust to outliers) with MinMaxScaler (bounds to [0,1]).",
                    "medium": "Explains removing nulls and scaling values, but misses algorithm-specific scaling requirements.",
                    "low": "Does not know why features need normalization before gradient-based optimization."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-f-04",
                "category": "Neural Networks Basics",
                "question": "How do backpropagation and gradient descent work in deep learning? Why are non-linear activation functions like ReLU necessary?",
                "expected_concepts": ["backpropagation", "gradient descent", "chain rule", "relu", "non-linear", "weights", "loss function"],
                "rubric": {
                    "high": "Explains chain rule calculating loss gradients with respect to weights; optimization updating parameters; notes without non-linear activations, multi-layer networks collapse to a single linear transformation.",
                    "medium": "Explains weights update to reduce loss, but cannot explain why non-linear activations prevent collapsing to a single linear layer.",
                    "low": "Cannot explain how a neural network learns from training data."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-f-05",
                "category": "Model Deployment",
                "question": "How do you package a trained PyTorch or Scikit-learn model and expose it as a high-performance REST API using FastAPI and Docker?",
                "expected_concepts": ["fastapi", "docker", "onnx", "serialization", "batching", "latency", "inference"],
                "rubric": {
                    "high": "Details model serialization (joblib, torchscript/ONNX); asynchronous FastAPI endpoints; pre-loading model weights on startup; containerization with Docker.",
                    "medium": "Mentions loading the model in an API route, but misses pre-loading on startup or containerization.",
                    "low": "Suggests retraining the model from scratch on every incoming API request."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "aiml-e-01",
                "category": "Transformer Architecture & LLMs",
                "question": "Explain the self-attention mechanism in the Transformer architecture. How do Multi-Head Attention and Positional Encodings work mathematically?",
                "expected_concepts": ["attention", "query", "key", "value", "softmax", "multi-head", "positional encoding", "transformers"],
                "rubric": {
                    "high": "Derives Attention(Q,K,V) = softmax(QK^T / sqrt(d_k))V; explains Multi-Head Attention projecting into distinct subspaces; sinusoidal or RoPE positional embeddings.",
                    "medium": "Describes Q, K, V conceptual roles, but lacks mathematical scaling factor rationale or multi-head subspace benefits.",
                    "low": "Confuses Transformers with Recurrent Neural Networks (RNNs)."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-e-02",
                "category": "Fine-Tuning & Quantization",
                "question": "What parameter-efficient fine-tuning (PEFT) methods like LoRA and QLoRA do you use to adapt open-source LLMs? How does 4-bit/8-bit quantization reduce VRAM requirements?",
                "expected_concepts": ["lora", "qlora", "peft", "quantization", "low-rank", "vram", "bitsandbytes", "weights"],
                "rubric": {
                    "high": "Explains Low-Rank Adaptation decomposing weight updates into rank r matrices (delta W = A * B); 4-bit NormalFloat quantization with double dequantization in QLoRA saving GPU VRAM.",
                    "medium": "Knows LoRA trains fewer weights and quantization saves memory, but cannot explain low-rank matrix decomposition.",
                    "low": "Believes full fine-tuning of every parameter is required for all domain adaptations."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-e-03",
                "category": "RAG Architecture",
                "question": "How do you architect an enterprise Retrieval-Augmented Generation (RAG) system with hybrid search, re-ranking, chunking strategies, and hallucination guardrails?",
                "expected_concepts": ["rag", "vector database", "embeddings", "hybrid search", "re-ranking", "chunking", "hallucination", "guardrails"],
                "rubric": {
                    "high": "Architects dense vector + sparse BM25 hybrid search; cross-encoder re-ranking; semantic chunking; and guardrails (NeMo, evaluation with RAGAS) to mitigate hallucination.",
                    "medium": "Describes standard vector database lookup with prompt injection, but omits re-ranking, hybrid search, or hallucination mitigation.",
                    "low": "Unaware of how embeddings and vector similarity search operate."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-e-04",
                "category": "MLOps & Drift Detection",
                "question": "How do you implement an end-to-end MLOps pipeline for automated model retraining, drift detection (data drift vs concept drift), and canary rollouts?",
                "expected_concepts": ["mlops", "data drift", "concept drift", "evidently", "mlflow", "feature store", "canary", "monitoring"],
                "rubric": {
                    "high": "Contrasts covariate/data drift (P(X) changes) with concept drift (P(Y|X) changes); details statistical tests (KS-test, PSI); MLflow registry; automated retraining triggers.",
                    "medium": "Knows models degrade over time and need retraining, but cannot distinguish data drift from concept drift.",
                    "low": "Assumes models remain equally accurate indefinitely after deployment."
                },
                "time_limit": 120
            },
            {
                "id": "aiml-e-05",
                "category": "High-Throughput Model Serving",
                "question": "Compare modern LLM inference engines (vLLM, TensorRT-LLM, Triton). How do continuous batching and PagedAttention optimize GPU throughput?",
                "expected_concepts": ["vllm", "triton", "tensorrt", "continuous batching", "pagedattention", "kv cache", "gpu memory"],
                "rubric": {
                    "high": "Explains KV cache memory fragmentation; PagedAttention inspired by OS virtual memory paging; continuous/dynamic iteration-level batching eliminating padding waste.",
                    "medium": "Knows vLLM accelerates generation, but cannot explain KV cache memory management or PagedAttention.",
                    "low": "Treats model inference as standard sequential CPU execution."
                },
                "time_limit": 120
            }
        ]
    },
    "cybersecurity": {
        "fresher": [
            {
                "id": "sec-f-01",
                "category": "Security Fundamentals",
                "question": "Explain the CIA Triad (Confidentiality, Integrity, Availability) and the difference between symmetric and asymmetric encryption.",
                "expected_concepts": ["confidentiality", "integrity", "availability", "symmetric", "asymmetric", "public key", "private key", "aes", "rsa"],
                "rubric": {
                    "high": "Defines all three pillars of CIA; explains symmetric uses same secret key (AES), asymmetric uses public/private keypair (RSA/ECC) for key exchange and signatures.",
                    "medium": "Defines CIA triad accurately, but confuses public/private key mechanisms in asymmetric encryption.",
                    "low": "Cannot name the components of the CIA Triad."
                },
                "time_limit": 120
            },
            {
                "id": "sec-f-02",
                "category": "OWASP Top 10",
                "question": "What are common vulnerabilities in the OWASP Top 10 (SQL Injection, Cross-Site Scripting, Broken Access Control)? How do you mitigate each?",
                "expected_concepts": ["owasp", "sql injection", "xss", "broken access control", "input validation", "prepared statements", "csrf"],
                "rubric": {
                    "high": "Explains SQL injection mitigated by prepared statements; XSS mitigated by output encoding/CSP; Broken Access Control mitigated by server-side authorization checks.",
                    "medium": "Identifies the vulnerability names but provides weak or incomplete mitigation steps.",
                    "low": "Confuses client-side JavaScript validation with actual security defense."
                },
                "time_limit": 120
            },
            {
                "id": "sec-f-03",
                "category": "Networking & Attack Vectors",
                "question": "How does the TCP three-way handshake work, and what is the difference between a SYN flood attack and an application-layer HTTP flood?",
                "expected_concepts": ["syn", "syn-ack", "ack", "handshake", "ddos", "syn flood", "layer 4", "layer 7"],
                "rubric": {
                    "high": "Details SYN, SYN-ACK, ACK handshake; Layer 4 SYN flood exhausting connection backlog with half-open sockets; Layer 7 HTTP flood exhausting server compute/database resources.",
                    "medium": "Explains 3-way handshake but cannot differentiate Layer 4 vs Layer 7 flood attack mechanics.",
                    "low": "Unclear on basic TCP connection establishment."
                },
                "time_limit": 120
            },
            {
                "id": "sec-f-04",
                "category": "Incident Response Lifecycle",
                "question": "What is the role of a Security Operations Center (SOC) analyst? Explain the six phases of the incident response lifecycle.",
                "expected_concepts": ["soc", "preparation", "identification", "containment", "eradication", "recovery", "lessons learned", "siem"],
                "rubric": {
                    "high": "Traces Preparation, Identification/Detection, Containment (isolation), Eradication (root cause removal), Recovery (restoration), and Lessons Learned (postmortem).",
                    "medium": "Knows detection and recovery, but omits containment or postmortem analysis.",
                    "low": "Suggests immediately wiping infected servers before preserving forensic evidence."
                },
                "time_limit": 120
            },
            {
                "id": "sec-f-05",
                "category": "Access Control & MFA",
                "question": "What is Multi-Factor Authentication (MFA), and what are the differences between Role-Based Access Control (RBAC) and Attribute-Based Access Control (ABAC)?",
                "expected_concepts": ["mfa", "rbac", "abac", "least privilege", "authorization", "authentication", "totp"],
                "rubric": {
                    "high": "Explains MFA factors (knowledge, possession, inherence); RBAC assigns permissions to static roles; ABAC evaluates dynamic context (time, IP, device security posture).",
                    "medium": "Explains 2FA and roles, but cannot articulate dynamic policy evaluation in ABAC.",
                    "low": "Confuses Authentication (who you are) with Authorization (what you can do)."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "sec-e-01",
                "category": "Zero Trust Architecture",
                "question": "How do you implement a Zero Trust security architecture across an enterprise cloud network? Cover identity verification, micro-segmentation, and continuous monitoring.",
                "expected_concepts": ["zero trust", "micro-segmentation", "least privilege", "identity provider", "mtls", "continuous evaluation"],
                "rubric": {
                    "high": "Articulates 'never trust, always verify'; identity as primary perimeter; network micro-segmentation preventing lateral movement; mTLS encryption; continuous posture evaluation.",
                    "medium": "Mentions VPN and firewalls, but fails to grasp zero-trust elimination of the implicit trusted internal perimeter.",
                    "low": "Assumes internal corporate network traffic should be implicitly trusted."
                },
                "time_limit": 120
            },
            {
                "id": "sec-e-02",
                "category": "Threat Hunting & SIEM",
                "question": "How do you leverage the MITRE ATT&CK framework with SIEM and EDR tools to detect advanced persistent threats (APTs) and lateral movement?",
                "expected_concepts": ["mitre att&ck", "siem", "edr", "lateral movement", "threat hunting", "apt", "pass the hash", "powershell"],
                "rubric": {
                    "high": "Maps detection rules to Tactics, Techniques, and Procedures (TTPs); correlates EDR process lineage (e.g. Living-off-the-Land binaries) and network telemetry; detects credential dumping.",
                    "medium": "Mentions SIEM log aggregation and alerts, but lacks MITRE ATT&CK TTP mapping or lateral movement detection.",
                    "low": "Relies solely on signature-based antivirus alerts."
                },
                "time_limit": 120
            },
            {
                "id": "sec-e-03",
                "category": "Penetration Testing & Exploitation",
                "question": "Walk me through how you conduct an authorized web application penetration test: reconnaissance, vulnerability mapping, exploitation, privilege escalation, and remediation reporting.",
                "expected_concepts": ["reconnaissance", "burp suite", "privilege escalation", "enumeration", "payload", "cve", "reporting"],
                "rubric": {
                    "high": "Details OSINT, Burp Suite proxying, fuzzing, testing auth boundaries and business logic flaws, safely demonstrating impact, and delivering remediation reports with CVSS scoring.",
                    "medium": "Describes automated vulnerability scanning, but lacks manual exploitation and business logic testing techniques.",
                    "low": "Cannot describe the steps of a structured penetration testing methodology."
                },
                "time_limit": 120
            },
            {
                "id": "sec-e-04",
                "category": "Cloud Security (AWS/Azure/GCP)",
                "question": "How do you detect and remediate IAM misconfigurations, excessive permissions, and unsecured cloud assets using CSPM and CIEM tools?",
                "expected_concepts": ["iam", "least privilege", "cspm", "s3 bucket", "kms", "cloudtrail", "guardduty", "policy"],
                "rubric": {
                    "high": "Details Cloud Security Posture Management (CSPM); enforcing IAM least privilege via permission boundaries; KMS encryption; auditing CloudTrail logs with GuardDuty anomaly detection.",
                    "medium": "Knows IAM roles and public bucket risks, but lacks automated posture management (CSPM/CIEM) architecture.",
                    "low": "Suggests using root account credentials for cloud automation."
                },
                "time_limit": 120
            },
            {
                "id": "sec-e-05",
                "category": "Cryptography & PKI",
                "question": "Explain Public Key Infrastructure (PKI), TLS handshake protocols, certificate revocation (CRL vs OCSP), and the transition to post-quantum cryptography.",
                "expected_concepts": ["pki", "certificate authority", "tls 1.3", "ocsp", "revocation", "post-quantum", "asymmetric"],
                "rubric": {
                    "high": "Details Root/Intermediate CA chains; TLS 1.3 1-RTT handshake; OCSP Stapling mitigating CRL latency/privacy; quantum threats to RSA/ECC and NIST lattice-based algorithms.",
                    "medium": "Explains HTTPS certificates and public keys, but lacks certificate revocation mechanics or quantum threat awareness.",
                    "low": "Cannot explain how a client verifies a certificate authority's digital signature."
                },
                "time_limit": 120
            }
        ]
    },
    "tester": {
        "fresher": [
            {
                "id": "qa-f-01",
                "category": "Testing Principles",
                "question": "Explain the testing pyramid (Unit, Integration, End-to-End). Why is having too many UI tests and too few unit tests considered an anti-pattern?",
                "expected_concepts": ["testing pyramid", "unit test", "integration test", "e2e", "flaky", "execution speed", "cost"],
                "rubric": {
                    "high": "Explains ice cream cone anti-pattern; unit tests are fast, cheap, and isolated; E2E tests are slow, expensive, and prone to flakiness; integration tests verify boundaries.",
                    "medium": "Defines each testing level accurately, but misses flakiness and cost implications of the inverted pyramid.",
                    "low": "Believes manual UI testing should replace automated unit testing."
                },
                "time_limit": 120
            },
            {
                "id": "qa-f-02",
                "category": "Test Design Techniques",
                "question": "What are Boundary Value Analysis (BVA) and Equivalence Class Partitioning (ECP)? Give an example of test cases derived using both techniques.",
                "expected_concepts": ["boundary value analysis", "equivalence partitioning", "test cases", "valid", "invalid", "edge cases"],
                "rubric": {
                    "high": "Explains dividing input domain into valid/invalid equivalence classes; BVA testing exact boundaries (min-1, min, min+1, max-1, max, max+1) where defects concentrate.",
                    "medium": "Defines the concepts with an example, but tests only inside partitions rather than boundary thresholds.",
                    "low": "Cannot differentiate boundary values from random exploratory tests."
                },
                "time_limit": 120
            },
            {
                "id": "qa-f-03",
                "category": "Automation Frameworks (Selenium/Playwright)",
                "question": "What is the Page Object Model (POM) in test automation, and why does it improve code maintainability and reduce test flakiness?",
                "expected_concepts": ["page object model", "pom", "maintainability", "selectors", "reusability", "locators"],
                "rubric": {
                    "high": "Explains encapsulating page UI locators and actions into dedicated classes; decoupling test assertions from locator changes; reducing code duplication across test suites.",
                    "medium": "Knows POM organizes page elements into classes, but lacks code maintainability explanation.",
                    "low": "Hardcodes locators repeatedly in every test script."
                },
                "time_limit": 120
            },
            {
                "id": "qa-f-04",
                "category": "API Testing",
                "question": "How do you automate REST API testing using tools like Postman, REST Assured, or pytest? What assertions do you verify beyond the status code?",
                "expected_concepts": ["api testing", "status code", "json schema", "response time", "headers", "assertions", "rest assured"],
                "rubric": {
                    "high": "Validates status codes, JSON schema contract, business response payload fields, response time SLAs, authentication headers, and negative test cases.",
                    "medium": "Mentions checking status code 200 and response body, without schema validation or performance assertions.",
                    "low": "Only checks if the API returns status 200."
                },
                "time_limit": 120
            },
            {
                "id": "qa-f-05",
                "category": "Defect Reporting",
                "question": "What essential information must be included in a high-quality defect report to ensure developers can reproduce and fix the issue quickly?",
                "expected_concepts": ["bug report", "steps to reproduce", "expected result", "actual result", "severity", "logs", "screenshots"],
                "rubric": {
                    "high": "Includes concise summary, environment/build info, exact step-by-step reproduction steps, expected vs actual behavior, severity/priority, screenshots/recordings, and console/server logs.",
                    "medium": "Mentions steps and screenshots, but omits environment details or console error logs.",
                    "low": "Files vague reports like 'the submit button does not work'."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "qa-e-01",
                "category": "Framework Architecture",
                "question": "How do you architect a scalable cross-browser automation framework from scratch using Playwright or Cypress with parallel execution and CI/CD integration?",
                "expected_concepts": ["playwright", "cypress", "parallel execution", "ci/cd", "reporting", "docker", "headless"],
                "rubric": {
                    "high": "Architects multi-threaded parallel workers; Dockerized headless runners; auto-waiting over arbitrary sleeps; artifact capture (traces, videos) on failure; CI/CD matrix integration.",
                    "medium": "Explains running tests in CI, but lacks parallel sharding or auto-waiting architecture.",
                    "low": "Relies on hardcoded Thread.sleep() calls across test suites."
                },
                "time_limit": 120
            },
            {
                "id": "qa-e-02",
                "category": "Performance & Load Testing",
                "question": "How do you design a load and stress testing strategy using JMeter, k6, or Locust to identify database connection bottlenecks and response degradation under peak traffic?",
                "expected_concepts": ["load testing", "stress testing", "k6", "jmeter", "locust", "throughput", "response time", "concurrency"],
                "rubric": {
                    "high": "Defines load curves (ramp-up, steady-state, spike, stress); correlates Virtual User throughput (RPS) with server CPU/memory and database connection pool saturation.",
                    "medium": "Runs a basic load test script, but cannot interpret percentiles (p95, p99) or pinpoint bottlenecks.",
                    "low": "Confuses single-user functional automation with multi-user load testing."
                },
                "time_limit": 120
            },
            {
                "id": "qa-e-03",
                "category": "Flakiness Mitigation",
                "question": "How do you detect, track, and eliminate flaky tests in a suite of 5,000 automated tests? How do explicit waits differ from arbitrary sleeps?",
                "expected_concepts": ["flaky tests", "explicit wait", "dynamic wait", "network idle", "retry logic", "quarantine"],
                "rubric": {
                    "high": "Details quarantine pipelines for unstable tests; replacing arbitrary sleeps with explicit condition polling; isolating shared test state; tracking flake rate metrics.",
                    "medium": "Uses test retries in CI, but treats retries as the final fix rather than eliminating root-cause flakiness.",
                    "low": "Ignores flaky test failures as unpreventable anomalies."
                },
                "time_limit": 120
            },
            {
                "id": "qa-e-04",
                "category": "Contract & Integration Testing",
                "question": "What is consumer-driven contract testing (e.g. Pact)? How does it verify microservice compatibility before deployment without running full E2E environments?",
                "expected_concepts": ["contract testing", "pact", "consumer-driven", "microservices", "mocking", "schema validation"],
                "rubric": {
                    "high": "Explains consumer publishing expectations (pact file); provider verifying contract in CI; enabling independent deployments without spinning up brittle full-system E2E environments.",
                    "medium": "Knows contract testing verifies API schemas, but cannot explain consumer-driven verification workflows.",
                    "low": "Believes full end-to-end multi-service deployment is mandatory for all integration verification."
                },
                "time_limit": 120
            },
            {
                "id": "qa-e-05",
                "category": "Test Data Management",
                "question": "How do you manage test data creation, isolation, and teardown across environments to ensure tests are deterministic and independent?",
                "expected_concepts": ["test data", "fixtures", "factories", "database seeding", "isolation", "deterministic", "teardown"],
                "rubric": {
                    "high": "Designs API-driven fixtures/factories creating ephemeral test accounts per test run; database transaction rollbacks; avoiding shared static state that causes cascading test failures.",
                    "medium": "Seeds a shared test database, but struggles when parallel tests collide on the same records.",
                    "low": "Manually enters test data into the UI before every automated run."
                },
                "time_limit": 120
            }
        ]
    },
    "devops": {
        "fresher": [
            {
                "id": "devops-f-01",
                "category": "Linux & System Diagnostics",
                "question": "Explain common Linux troubleshooting commands for checking active processes, disk space, and open network sockets (ps, top/htop, df, du, netstat/ss, lsof).",
                "expected_concepts": ["linux", "ps", "top", "df", "du", "netstat", "ss", "lsof", "processes", "disk"],
                "rubric": {
                    "high": "Accurately maps ps/top to CPU/memory consumption; df/du to disk capacity vs directory usage; ss/netstat and lsof to open ports and listening process IDs.",
                    "medium": "Names several commands correctly, but confuses df (filesystem) with du (directory usage).",
                    "low": "Cannot remember commands for basic process inspection."
                },
                "time_limit": 120
            },
            {
                "id": "devops-f-02",
                "category": "Docker & Containers",
                "question": "What is the difference between a virtual machine and a Docker container? How do you optimize a Dockerfile using multi-stage builds?",
                "expected_concepts": ["docker", "container", "virtual machine", "kernel", "multi-stage build", "image size", "layers"],
                "rubric": {
                    "high": "Contrasts VM guest OS hypervisor virtualization with container shared host kernel cgroups/namespaces; multi-stage builds separating build tools from minimal runtime images.",
                    "medium": "Knows containers are lighter than VMs, but cannot explain multi-stage Docker builds.",
                    "low": "Thinks Docker containers run complete hardware hypervisors."
                },
                "time_limit": 120
            },
            {
                "id": "devops-f-03",
                "category": "CI/CD Pipelines",
                "question": "Explain the stages of a modern CI/CD pipeline using GitHub Actions, GitLab CI, or Jenkins from code commit to production deployment.",
                "expected_concepts": ["ci/cd", "pipeline", "linting", "unit tests", "build", "artifact", "deploy", "staging", "production"],
                "rubric": {
                    "high": "Traces commit triggers, automated linting/formatting, unit/integration testing, container build & vulnerability scan, artifact push, staging deployment, and production gate.",
                    "medium": "Outlines build, test, and deploy, but misses automated artifact packaging or staging verification.",
                    "low": "Confuses continuous integration with periodic manual deployments."
                },
                "time_limit": 120
            },
            {
                "id": "devops-f-04",
                "category": "Kubernetes Core",
                "question": "Explain core Kubernetes building blocks: Pods, Deployments, Services (ClusterIP, NodePort, LoadBalancer), and Ingress controllers.",
                "expected_concepts": ["kubernetes", "pod", "deployment", "service", "clusterip", "nodeport", "loadbalancer", "ingress"],
                "rubric": {
                    "high": "Explains Pod as atomic unit; Deployment managing replica sets and rolling updates; Service networking types (internal ClusterIP vs external LoadBalancer); Ingress Layer 7 routing.",
                    "medium": "Defines Pods and Deployments, but confuses Service networking types with Ingress controllers.",
                    "low": "Cannot explain what a Kubernetes Pod contains."
                },
                "time_limit": 120
            },
            {
                "id": "devops-f-05",
                "category": "Monitoring & Golden Signals",
                "question": "What are the four golden signals of monitoring (latency, traffic, errors, saturation)? How do Prometheus and Grafana work together?",
                "expected_concepts": ["latency", "traffic", "errors", "saturation", "prometheus", "grafana", "metrics", "alerts"],
                "rubric": {
                    "high": "Defines Latency (request duration), Traffic (RPS), Errors (rate of failed requests), and Saturation (system resource fullness); Prometheus scraping time-series and Grafana visualizing dashboards.",
                    "medium": "Names latency and errors, but misses saturation or how Prometheus pull-based metrics scraping operates.",
                    "low": "Confuses metrics monitoring with log text searching."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "devops-e-01",
                "category": "Infrastructure as Code & GitOps",
                "question": "How do you structure enterprise Terraform code with state locking, remote backends (S3/DynamoDB), modularization, and GitOps with ArgoCD?",
                "expected_concepts": ["terraform", "state locking", "s3", "dynamodb", "modules", "argocd", "gitops", "drift"],
                "rubric": {
                    "high": "Details remote state storage with DynamoDB concurrency locking; reusable modules; environment isolation; ArgoCD reconciling declarative Git manifests against cluster state.",
                    "medium": "Uses Terraform and modules, but lacks state locking knowledge or GitOps reconciliation mechanics.",
                    "low": "Stores Terraform state files directly in public Git repositories."
                },
                "time_limit": 120
            },
            {
                "id": "devops-e-02",
                "category": "Kubernetes Networking & Service Mesh",
                "question": "Explain how Kubernetes CNI plugins and Service Meshes (Istio, Linkerd) handle mTLS encryption, traffic shifting (canary routing), and distributed tracing.",
                "expected_concepts": ["service mesh", "istio", "cni", "mtls", "canary", "envoy", "sidecar", "distributed tracing"],
                "rubric": {
                    "high": "Explains CNI IP allocation; Envoy sidecar proxies intercepting pod traffic; automated mutual TLS; weighted canary routing in VirtualServices; propagating tracing headers.",
                    "medium": "Knows Istio provides security and routing, but cannot explain sidecar proxy architecture or header propagation.",
                    "low": "Unaware of what a Kubernetes Service Mesh does."
                },
                "time_limit": 120
            },
            {
                "id": "devops-e-03",
                "category": "SRE Principles & Error Budgets",
                "question": "How do you define Service Level Indicators (SLIs), Service Level Objectives (SLOs), and Error Budgets? What policy actions occur when an error budget is exhausted?",
                "expected_concepts": ["sli", "slo", "sla", "error budget", "reliability", "burn rate", "feature freeze", "postmortem"],
                "rubric": {
                    "high": "Defines SLI (quantifiable metric), SLO (internal target e.g. 99.9%), Error Budget (1 - SLO); when budget burns, halts non-essential feature rollouts to prioritize reliability engineering.",
                    "medium": "Defines SLA and SLO, but lacks understanding of error budget burn rates or governance actions.",
                    "low": "Treats 100% uptime as a realistic and mandatory engineering goal."
                },
                "time_limit": 120
            },
            {
                "id": "devops-e-04",
                "category": "Disaster Recovery & Multi-Region",
                "question": "How do you design a multi-region active-active or active-passive disaster recovery architecture with RPO (Recovery Point Objective) and RTO (Recovery Time Objective)?",
                "expected_concepts": ["rpo", "rto", "multi-region", "active-active", "failover", "dns routing", "replication"],
                "rubric": {
                    "high": "Contrasts RPO (acceptable data loss window) with RTO (acceptable downtime duration); global DNS routing (Route 53 / Cloudflare); cross-region database replication trade-offs.",
                    "medium": "Explains backup and restore, but struggles to define quantitative RPO/RTO metrics.",
                    "low": "Confuses local server rebooting with multi-region disaster recovery."
                },
                "time_limit": 120
            },
            {
                "id": "devops-e-05",
                "category": "DevSecOps & Secrets Management",
                "question": "How do you implement centralized secrets management (HashiCorp Vault, AWS Secrets Manager), vulnerability scanning (Trivy), and automated supply chain security in CI/CD?",
                "expected_concepts": ["vault", "secrets manager", "trivy", "supply chain", "image scanning", "least privilege", "rbac"],
                "rubric": {
                    "high": "Details short-lived dynamic credentials in Vault; static code analysis (SAST) and container vulnerability scanning (Trivy) gating pipelines; SBOM generation for supply chain integrity.",
                    "medium": "Uses CI secrets environment variables, but lacks dynamic secret leases or supply chain artifact verification.",
                    "low": "Commits environment passwords into container image layers."
                },
                "time_limit": 120
            }
        ]
    },
    "cloud": {
        "fresher": [
            {
                "id": "cloud-f-01",
                "category": "Cloud Computing Models",
                "question": "Explain the differences between IaaS, PaaS, and SaaS, and describe the Cloud Shared Responsibility Model for security.",
                "expected_concepts": ["iaas", "paas", "saas", "shared responsibility", "infrastructure", "managed", "security"],
                "rubric": {
                    "high": "Accurately maps IaaS (EC2), PaaS (Heroku/Elastic Beanstalk), and SaaS (Office365); explains cloud provider secures 'of the cloud' while customer secures 'in the cloud'.",
                    "medium": "Defines acronyms, but struggles with the boundaries of the Shared Responsibility Model.",
                    "low": "Believes the cloud provider is 100% responsible for customer data and application security."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-f-02",
                "category": "VPC & Networking",
                "question": "How do Virtual Private Clouds (VPCs), subnets (public vs private), Internet Gateways, and NAT Gateways work together to isolate backend workloads?",
                "expected_concepts": ["vpc", "subnet", "public", "private", "internet gateway", "nat gateway", "cidr", "security group"],
                "rubric": {
                    "high": "Details public subnets with Internet Gateway route; private subnets routing outbound traffic via NAT Gateway without exposing public IPs; Security Groups acting as stateful firewalls.",
                    "medium": "Explains public vs private subnets, but confuses Internet Gateway with NAT Gateway responsibilities.",
                    "low": "Assigns public IP addresses to all database instances."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-f-03",
                "category": "Serverless Architecture",
                "question": "What is Serverless computing (AWS Lambda / Azure Functions)? What are cold starts, and how do event-driven triggers work?",
                "expected_concepts": ["serverless", "lambda", "cold start", "event-driven", "scaling", "s3", "dynamodb", "stateless"],
                "rubric": {
                    "high": "Explains on-demand compute without server management; micro-billing per invocation; cold start initialization latency; event triggers from S3, SQS, or API Gateway.",
                    "medium": "Knows Lambda executes functions without dedicated VMs, but lacks understanding of cold starts or execution timeouts.",
                    "low": "Thinks serverless means no physical servers exist in the datacenter."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-f-04",
                "category": "Object Storage & CDN",
                "question": "Compare block storage (EBS), file storage (EFS), and object storage (S3). When would you front S3 with CloudFront CDN?",
                "expected_concepts": ["s3", "ebs", "efs", "object storage", "block storage", "cloudfront", "cdn", "latency"],
                "rubric": {
                    "high": "Contrasts EBS attached block volume with EFS multi-mount network filesystem and S3 REST-accessible object store; CloudFront edge caching reducing origin latency and egress cost.",
                    "medium": "Knows S3 stores files and EBS acts like a hard drive, but cannot explain CloudFront CDN caching benefits.",
                    "low": "Confuses object storage with relational database tables."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-f-05",
                "category": "IAM & Least Privilege",
                "question": "What is IAM (Identity & Access Management)? Explain the differences between IAM Users, Groups, Roles, and Policies.",
                "expected_concepts": ["iam", "users", "groups", "roles", "policies", "least privilege", "assume role", "temporary credentials"],
                "rubric": {
                    "high": "Explains JSON policies granting permissions; Users representing humans; Roles assumed by services/resources via temporary STS credentials following least privilege.",
                    "medium": "Defines users and policies, but cannot explain why EC2 instances should assume IAM roles rather than storing long-lived access keys.",
                    "low": "Embeds root AWS access keys directly in application source code."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "cloud-e-01",
                "category": "Cloud Architecture & Well-Architected Framework",
                "question": "Explain the six pillars of the AWS Well-Architected Framework (Operational Excellence, Security, Reliability, Performance Efficiency, Cost Optimization, Sustainability).",
                "expected_concepts": ["well-architected", "security", "reliability", "performance", "cost optimization", "sustainability", "operational excellence"],
                "rubric": {
                    "high": "Walks through each pillar with architectural design decisions: automated infrastructure, defense in depth, failure recovery, right-sizing resources, and reducing idle compute footprint.",
                    "medium": "Names several pillars, but provides superficial explanations of cost optimization or reliability engineering.",
                    "low": "Unfamiliar with the Well-Architected Framework."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-e-02",
                "category": "Multi-Account Governance",
                "question": "How do you design an enterprise multi-account AWS organization using AWS Organizations, Service Control Policies (SCPs), and Transit Gateways?",
                "expected_concepts": ["aws organizations", "scp", "transit gateway", "landing zone", "control tower", "isolation"],
                "rubric": {
                    "high": "Architects separate accounts for security, shared services, dev, and prod; SCPs setting guardrail boundaries; Transit Gateway centralizing spoke VPC routing and VPN/Direct Connect.",
                    "medium": "Knows having separate dev and prod accounts is good practice, but lacks SCP guardrail or Transit Gateway routing architecture.",
                    "low": "Proposes deploying enterprise production and sandbox environments into a single unsegregated AWS account."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-e-03",
                "category": "FinOps & Cloud Cost Optimization",
                "question": "How do you implement FinOps practices to optimize cloud spend across compute (Savings Plans/Spot instances), storage lifecycle tiers, and network egress?",
                "expected_concepts": ["finops", "savings plans", "reserved instances", "spot", "s3 glacier", "lifecycle", "cost allocation tags"],
                "rubric": {
                    "high": "Balances baseline compute with Compute Savings Plans; Spot instances for fault-tolerant workers; S3 lifecycle policies transitioning to Glacier; analyzing data egress routes.",
                    "medium": "Mentions shutting down dev servers at night, but lacks understanding of Savings Plans commitments or storage tiering.",
                    "low": "Leaves high-memory on-demand instances running permanently without monitoring."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-e-04",
                "category": "Hybrid Cloud & Direct Connect",
                "question": "How do you establish secure, high-throughput dedicated connectivity between an on-premises datacenter and a public cloud using Direct Connect / ExpressRoute and IPsec VPN failover?",
                "expected_concepts": ["direct connect", "expressroute", "ipsec vpn", "bgp", "failover", "hybrid cloud", "transit gateway"],
                "rubric": {
                    "high": "Architects dedicated 10Gbps Direct Connect circuits with BGP routing; automatic failover to IPsec VPN tunnels; Transit Gateway peering; private virtual interfaces.",
                    "medium": "Knows Direct Connect is a physical cable to the cloud, but misses BGP routing or VPN failover architecture.",
                    "low": "Suggests routing all internal datacenter database traffic over the public internet without encryption."
                },
                "time_limit": 120
            },
            {
                "id": "cloud-e-05",
                "category": "Disaster Recovery & Multi-Cloud",
                "question": "What are the real-world trade-offs and architectural complexities of multi-cloud strategies? When does vendor lock-in outweigh the cost of cloud-agnostic abstractions?",
                "expected_concepts": ["multi-cloud", "vendor lock-in", "kubernetes", "data egress", "complexity", "terraform", "identity"],
                "rubric": {
                    "high": "Critiques multi-cloud lowest-common-denominator traps; high cross-cloud egress costs and operational complexity; emphasizes using cloud-native managed services unless regulatory mandates require multi-vendor.",
                    "medium": "Advocates for Kubernetes to be cloud-agnostic, without accounting for egress costs or multi-cloud operational overhead.",
                    "low": "Believes multi-cloud is trivial with no latency, cost, or operational trade-offs."
                },
                "time_limit": 120
            }
        ]
    },
    "hr": {
        "fresher": [
            {
                "id": "hr-f-01",
                "category": "Self Introduction & Background",
                "question": "Can you walk me through your background, your key technical projects, and what motivated you to pursue a career in technology?",
                "expected_concepts": ["background", "education", "passion", "projects", "motivation", "goals", "skills"],
                "rubric": {
                    "high": "Presents a structured, concise introduction highlighting academic focus, hands-on projects, passion for solving problems, and clear career motivation.",
                    "medium": "Recites resume chronology without explaining motivation or personal passion.",
                    "low": "Unprepared, disjointed, or excessively brief answer."
                },
                "time_limit": 120
            },
            {
                "id": "hr-f-02",
                "category": "Teamwork & Collaboration",
                "question": "Can you share an experience where you had to collaborate on a team project? How did you handle a situation where members had conflicting ideas?",
                "expected_concepts": ["teamwork", "collaboration", "communication", "listening", "compromise", "success"],
                "rubric": {
                    "high": "Demonstrates active listening, open dialogue, focusing on objective project requirements, and reaching consensus through professional compromise.",
                    "medium": "Describes a team project but focuses solely on own contributions without detailing how conflicts were resolved.",
                    "low": "Blames teammates or claims never having had any difference of opinion."
                },
                "time_limit": 120
            },
            {
                "id": "hr-f-03",
                "category": "Problem Solving & Adaptability",
                "question": "Describe a scenario where you faced a significant obstacle or technical hurdle while working on a project. How did you overcome it?",
                "expected_concepts": ["obstacle", "challenge", "problem solving", "resilience", "learning", "outcome"],
                "rubric": {
                    "high": "Applies STAR method (Situation, Task, Action, Result); demonstrates structured troubleshooting, research resilience, and learning from the outcome.",
                    "medium": "Mentions a problem and solution, but misses structured explanation of personal actions taken.",
                    "low": "Claims never having faced an obstacle or gave up when stuck."
                },
                "time_limit": 120
            },
            {
                "id": "hr-f-04",
                "category": "Continuous Learning",
                "question": "Technology moves rapidly. How do you keep your technical skills current, and what is something new you learned recently?",
                "expected_concepts": ["learning", "courses", "curiosity", "documentation", "projects", "growth mindset"],
                "rubric": {
                    "high": "Names specific credible resources (documentation, open source, technical books, tech talks); explains a recent concept mastered through practical experimentation.",
                    "medium": "Mentions general online videos without a concrete recent learning example.",
                    "low": "Admits to only studying for exams or when assigned tasks."
                },
                "time_limit": 120
            },
            {
                "id": "hr-f-05",
                "category": "Career Aspirations",
                "question": "Where do you see yourself professionally in the next three to five years, and what are you looking for in an ideal engineering team?",
                "expected_concepts": ["career goals", "growth", "mentorship", "impact", "skills", "values"],
                "rubric": {
                    "high": "Balances technical depth progression with growing ownership and mentorship; seeks collaborative team with high code standards and supportive feedback culture.",
                    "medium": "Expresses general desire to become a senior engineer, but lacks specific skill progression path.",
                    "low": "Unrealistic goals or indifference toward team culture."
                },
                "time_limit": 120
            }
        ],
        "experienced": [
            {
                "id": "hr-e-01",
                "category": "Leadership & Mentorship",
                "question": "Tell me about a time you mentored a junior engineer or guided a team through a complex delivery deadline. How did you ensure quality and morale?",
                "expected_concepts": ["mentorship", "leadership", "delegation", "quality", "empathy", "delivery", "morale"],
                "rubric": {
                    "high": "Demonstrates empathetic leadership; pair programming; breaking down complex tasks; shielding team from thrash while maintaining high code review standards.",
                    "medium": "Describes assigning tasks and reviewing code, but lacks mentorship philosophy or morale considerations.",
                    "low": "Micromanages team or takes full credit for junior engineers' work."
                },
                "time_limit": 120
            },
            {
                "id": "hr-e-02",
                "category": "Conflict Resolution & Stakeholder Management",
                "question": "Describe a scenario where you strongly disagreed with a product manager or technical lead regarding technical direction. How did you reach alignment?",
                "expected_concepts": ["disagreement", "alignment", "stakeholders", "data-driven", "compromise", "listening", "tradeoffs"],
                "rubric": {
                    "high": "Anchors disagreement in user/business impact and data; listens to stakeholder constraints; presents structured trade-offs; commits fully once a decision is made ('disagree and commit').",
                    "medium": "Describes a disagreement resolved by executive mandate rather than constructive consensus.",
                    "low": "Resists decisions stubbornly or engages in passive-aggressive compliance."
                },
                "time_limit": 120
            },
            {
                "id": "hr-e-03",
                "category": "Crisis Management & Postmortems",
                "question": "Can you walk me through a major production incident or outage you were involved with? How did you manage the triage, communication, and blameless postmortem?",
                "expected_concepts": ["outage", "production incident", "triage", "blameless postmortem", "root cause", "prevention"],
                "rubric": {
                    "high": "Prioritizes customer mitigation first; transparent stakeholder communication; blameless postmortem investigating systemic root causes; actionable preventive safeguards.",
                    "medium": "Explains how the bug was fixed, but lacks incident communication or blameless culture.",
                    "low": "Focuses on assigning individual blame for the outage."
                },
                "time_limit": 120
            },
            {
                "id": "hr-e-04",
                "category": "Engineering Ethics & Integrity",
                "question": "Have you ever faced a situation where you were pressured to cut corners on security, testing, or compliance to meet a tight deadline? How did you respond?",
                "expected_concepts": ["ethics", "security", "compliance", "transparency", "risk escalation", "compromise", "integrity"],
                "rubric": {
                    "high": "Stands firm on non-negotiable security and legal compliance; transparently communicates trade-offs; proposes reducing scope rather than compromising security or data integrity.",
                    "medium": "Mentions avoiding security risks, but struggles with the practical escalation path when under executive pressure.",
                    "low": "Admits to secretly disabling tests or security controls to ship faster."
                },
                "time_limit": 120
            },
            {
                "id": "hr-e-05",
                "category": "Strategic Technical Vision",
                "question": "What engineering methodologies or architectural paradigms do you see becoming essential for our industry over the next few years, and how are you preparing?",
                "expected_concepts": ["ai integration", "cloud native", "observability", "distributed systems", "continuous delivery", "developer productivity"],
                "rubric": {
                    "high": "Articulates forward-looking trends (AI-augmented engineering, edge computing, distributed observability, zero-trust security) grounded in practical developer experience.",
                    "medium": "Names current buzzwords without deep insight into architectural consequences.",
                    "low": "Unaware of modern architectural evolution."
                },
                "time_limit": 120
            }
        ]
    }
}

# Add alias mappings for web, tester, qa, fullstack, devops, etc.
QUESTION_BANK["web"] = QUESTION_BANK["frontend"]

def normalize_role_key(role_str: str) -> str:
    """Normalizes arbitrary user role input into known keys or 'custom'."""
    r = (role_str or "").lower().strip()
    if any(k in r for k in ["python"]):
        return "python"
    if any(k in r for k in ["java"]):
        return "java"
    if any(k in r for k in ["fullstack", "full stack", "full-stack", "mern", "mean"]):
        return "fullstack"
    if any(k in r for k in ["front", "react", "ui", "ux", "angular", "vue", "web"]):
        return "frontend"
    if any(k in r for k in ["back", "node", "django", "fastapi", "spring", "api", "microservice"]):
        return "backend"
    if any(k in r for k in ["data", "analytics", "bi", "sql", "tableau", "powerbi"]):
        return "data_analyst"
    if any(k in r for k in ["ai", "ml", "machine learning", "deep learning", "nlp", "llm", "vision"]):
        return "ai_ml"
    if any(k in r for k in ["cyber", "security", "soc", "infosec", "pen", "hack"]):
        return "cybersecurity"
    if any(k in r for k in ["qa", "tester", "test", "automation", "sdet", "selenium"]):
        return "tester"
    if any(k in r for k in ["devops", "sre", "ci/cd", "kubernetes", "docker", "terraform"]):
        return "devops"
    if any(k in r for k in ["cloud", "aws", "azure", "gcp"]):
        return "cloud"
    if any(k in r for k in ["hr", "behavioral", "human resource", "culture"]):
        return "hr"
    return "custom"

def generate_custom_role_questions(role_title: str, experience_level: str, skills: str = "") -> List[Dict[str, Any]]:
    """
    Generates structured, role-specific questions for any custom or specialized role.
    """
    is_exp = "exp" in experience_level.lower() or "senior" in experience_level.lower()
    clean_role = role_title.strip() if role_title.strip() else "Software Specialist"
    skills_ctx = f" (Focus: {skills.strip()})" if skills.strip() else ""

    if is_exp:
        return [
            {
                "id": "custom-e-01",
                "category": f"{clean_role} Architecture",
                "question": f"As an experienced {clean_role}{skills_ctx}, what core architectural patterns and design principles do you apply when developing high-performance, scalable systems?",
                "expected_concepts": ["architecture", "design patterns", "scalability", "tradeoffs", "performance", "modularity", "clean code"],
                "rubric": {
                    "high": f"Articulates mature design choices, system modularity, and trade-offs specific to {clean_role}.",
                    "medium": "Discusses general architecture but lacks domain-specific patterns.",
                    "low": "Superficial answer without architectural depth."
                },
                "time_limit": 120
            },
            {
                "id": "custom-e-02",
                "category": "Optimization & Deep Dive",
                "question": f"In your work as a {clean_role}, describe a complex technical performance bottleneck you diagnosed and optimized under tight constraints.",
                "expected_concepts": ["profiling", "bottleneck", "optimization", "metrics", "root cause", "latency", "throughput"],
                "rubric": {
                    "high": "Details diagnostic profiling, root-cause identification, and concrete measurable optimization results.",
                    "medium": "Mentions fixing a bug, but misses structured performance diagnostics.",
                    "low": "Cannot articulate a concrete optimization scenario."
                },
                "time_limit": 120
            },
            {
                "id": "custom-e-03",
                "category": "Reliability & Edge Cases",
                "question": f"How do you design for fault tolerance, edge cases, and automated failure recovery in your {clean_role} workflows?",
                "expected_concepts": ["fault tolerance", "edge cases", "resilience", "recovery", "validation", "monitoring", "fallback"],
                "rubric": {
                    "high": "Explains defensive programming, automated fallbacks, and boundary validations.",
                    "medium": "Mentions basic error handling without system-level fault tolerance.",
                    "low": "Neglects edge cases or failure modes."
                },
                "time_limit": 120
            },
            {
                "id": "custom-e-04",
                "category": "Testing & Quality Assurance",
                "question": f"What testing strategies, CI/CD pipelines, and automated quality gates do you mandate across your {clean_role} codebase?",
                "expected_concepts": ["testing", "ci/cd", "automation", "quality gates", "integration", "code review", "coverage"],
                "rubric": {
                    "high": "Outlines multi-layered testing, automated pipeline gates, and code review governance.",
                    "medium": "Describes basic unit testing without end-to-end pipeline automation.",
                    "low": "Relies primarily on manual ad-hoc testing."
                },
                "time_limit": 120
            },
            {
                "id": "custom-e-05",
                "category": "Technical Leadership & Trade-offs",
                "question": f"How do you evaluate and balance technical debt against rapid business feature delivery in your {clean_role} responsibilities?",
                "expected_concepts": ["technical debt", "business value", "refactoring", "stakeholders", "prioritization", "long term"],
                "rubric": {
                    "high": "Balances pragmatism with architectural integrity; transparently quantifies technical debt risk to stakeholders.",
                    "medium": "Acknowledges technical debt but lacks structured prioritization framework.",
                    "low": "Advocates either ignoring technical debt completely or blocking all business deliveries."
                },
                "time_limit": 120
            }
        ]
    else:
        return [
            {
                "id": "custom-f-01",
                "category": f"{clean_role} Fundamentals",
                "question": f"As an aspiring {clean_role}{skills_ctx}, what fundamental concepts, tools, and workflows form the foundation of your technical skillset?",
                "expected_concepts": ["fundamentals", "workflow", "tools", "syntax", "data structures", "best practices"],
                "rubric": {
                    "high": f"Demonstrates solid conceptual clarity on core principles, tools, and foundational workflows of {clean_role}.",
                    "medium": "Mentions tools used without explaining core underlying concepts.",
                    "low": "Struggles to define basic terminology."
                },
                "time_limit": 120
            },
            {
                "id": "custom-f-02",
                "category": "Practical Project Experience",
                "question": f"Can you walk me through a technical project you built related to {clean_role}? What was your approach and what technical challenges did you solve?",
                "expected_concepts": ["project", "implementation", "problem solving", "approach", "results", "technologies"],
                "rubric": {
                    "high": "Structured STAR explanation of problem statement, architecture, implementation hurdles, and outcomes.",
                    "medium": "Lists features of a tutorial project without deep implementation ownership.",
                    "low": "Cannot clearly explain how their project works."
                },
                "time_limit": 120
            },
            {
                "id": "custom-f-03",
                "category": "Debugging & Troubleshooting",
                "question": f"When your code or workflow produces an unexpected error as a {clean_role}, what step-by-step troubleshooting methodology do you use?",
                "expected_concepts": ["debugging", "logs", "reproduction", "stack trace", "isolation", "testing", "documentation"],
                "rubric": {
                    "high": "Systematic debugging: reading stack traces, isolating variables, inspecting logs, and writing regression tests.",
                    "medium": "Describes trial-and-error changes or searching online without methodical root-cause isolation.",
                    "low": "Does not know how to read error messages."
                },
                "time_limit": 120
            },
            {
                "id": "custom-f-04",
                "category": "Best Practices & Standards",
                "question": f"What coding standards, documentation habits, and version control practices do you follow to ensure your work is clean and collaborative?",
                "expected_concepts": ["git", "version control", "documentation", "clean code", "readability", "comments", "standards"],
                "rubric": {
                    "high": "Highlights Git feature branching, clear commit messages, meaningful variable naming, and useful documentation.",
                    "medium": "Mentions using Git for backup without collaborative branching or clean code principles.",
                    "low": "Unfamiliar with version control."
                },
                "time_limit": 120
            },
            {
                "id": "custom-f-05",
                "category": "Learning & Adaptability",
                "question": f"How do you approach learning a completely new framework, library, or tool when tackling unfamiliar requirements in {clean_role}?",
                "expected_concepts": ["documentation", "hands-on", "curiosity", "experimentation", "learning", "growth"],
                "rubric": {
                    "high": "Reads official documentation, builds small prototypes, learns through hands-on failure, and consults community best practices.",
                    "medium": "Relies exclusively on tutorial videos without consulting documentation.",
                    "low": "Resistant to learning tools outside comfort zone."
                },
                "time_limit": 120
            }
        ]


class InterviewEvaluationEngine:
    """
    Intelligent NLP & Rubric-based evaluation engine for candidate answers.
    Evaluates technical correctness, concept coverage, explanation depth, examples, and communication.
    Supports optional LLM API if configured via environment variables, with transparent fallback labeling.
    """

    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    def get_questions_for_session(
        self,
        role: str,
        experience_level: str,
        skills: str = "",
        custom_role: str = "",
        question_count: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieves or generates curated questions matching role, experience level, skills, and requested question count.
        Prioritizes curated role question banks for instant (<1ms) load times, falling back to
        AI generation or custom role templates for specialized tracks.
        """
        effective_role = custom_role.strip() if custom_role and custom_role.strip() else role.strip()
        norm_level = "experienced" if ("exp" in experience_level.lower() or "senior" in experience_level.lower()) else "fresher"
        target_count = max(1, int(question_count)) if question_count else 5

        # 1. Check curated role question bank first for instant response
        role_key = normalize_role_key(effective_role)
        questions = []
        if role_key in QUESTION_BANK:
            curated = list(QUESTION_BANK[role_key].get(norm_level, []))
            questions.extend(curated)

            # If user requested more questions than in the primary set (e.g. 7 or 10), supplement
            if len(questions) < target_count:
                opp_level = "experienced" if norm_level == "fresher" else "fresher"
                opp_curated = QUESTION_BANK[role_key].get(opp_level, [])
                for q in opp_curated:
                    if len(questions) >= target_count:
                        break
                    if q["id"] not in [item["id"] for item in questions]:
                        questions.append(q)

            if questions:
                logger.info(f"Loaded {len(questions[:target_count])} curated questions for role={effective_role} (key={role_key}, level={norm_level}, requested={target_count})")
                if len(questions) >= target_count:
                    return questions[:target_count]

        # 2. Try AI generation if API key is present for custom / non-standard roles
        if self.gemini_api_key or self.openai_api_key:
            try:
                ai_questions = self._generate_questions_with_ai(effective_role, norm_level, skills)
                if ai_questions and len(ai_questions) >= 3:
                    logger.info(f"Successfully generated {len(ai_questions)} dynamic questions via AI for {effective_role}")
                    return ai_questions[:target_count]
            except Exception as e:
                logger.warning(f"AI question generation failed, falling back to custom role generator: {e}")

        # 3. Dynamic custom role generator fallback
        custom_pool = list(generate_custom_role_questions(effective_role, norm_level, skills))
        all_pool = questions + [q for q in custom_pool if q["id"] not in [x["id"] for x in questions]]
        return all_pool[:target_count]

    def _generate_questions_with_ai(
        self,
        role: str,
        experience_level: str,
        skills: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Calls Gemini or OpenAI to dynamically formulate role and skill-tailored questions."""
        prompt = (
            f"You are an expert technical interviewer conducting an interview for the role of '{role}'.\n"
            f"Experience Level: {experience_level}.\n"
            f"Candidate Skills: {skills if skills else 'Core role skills'}.\n\n"
            f"Generate exactly 5 focused, high-impact technical interview questions for this candidate.\n"
            f"Respond ONLY with a valid JSON array of 5 objects with this exact structure:\n"
            f"[\n"
            f"  {{\n"
            f"    \"id\": \"ai-q-1\",\n"
            f"    \"category\": \"Short category name\",\n"
            f"    \"question\": \"The clear, focused question text?\",\n"
            f"    \"expected_concepts\": [\"concept1\", \"concept2\", \"concept3\", \"concept4\"],\n"
            f"    \"rubric\": {{\n"
            f"      \"high\": \"Criteria for top answer\",\n"
            f"      \"medium\": \"Criteria for average answer\",\n"
            f"      \"low\": \"Criteria for poor answer\"\n"
            f"    }},\n"
            f"    \"time_limit\": 120\n"
            f"  }}\n"
            f"]\n"
            f"Do not include markdown fences, backticks, or other text outside the JSON array."
        )

        # Call Gemini if key available
        if self.gemini_api_key and self.gemini_api_key not in ("YOUR_API_KEY_HERE", "DUMMY_KEY"):
            models = ["gemini-1.5-flash", "gemini-2.0-flash"]
            for model_name in models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.gemini_api_key}"
                    headers = {"Content-Type": "application/json"}
                    payload = json.dumps({
                        "contents": [{"parts": [{"text": prompt}]}]
                    }).encode("utf-8")

                    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=3) as response:
                        res_json = json.loads(response.read().decode("utf-8"))
                        text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                        cleaned = self._clean_json_text(text)
                        parsed = json.loads(cleaned)
                        if isinstance(parsed, list) and len(parsed) >= 3:
                            return parsed
                except Exception as ex:
                    logger.debug(f"Gemini {model_name} question generation error: {ex}")
                    continue

        # Call OpenAI if key available
        if self.openai_api_key and self.openai_api_key.startswith("sk-"):
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_api_key}"
                }
                payload = json.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4
                }).encode("utf-8")

                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=8) as response:
                    res_json = json.loads(response.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    cleaned = self._clean_json_text(text)
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list) and len(parsed) >= 3:
                        return parsed
            except Exception as ex:
                logger.debug(f"OpenAI question generation error: {ex}")

        return None

    def evaluate_answer(
        self,
        question_data: Dict[str, Any],
        candidate_transcript: str,
        role: str,
        experience_level: str
    ) -> Dict[str, Any]:
        """
        Evaluates a candidate's transcribed spoken or typed answer against reference concepts and rubric.
        Returns score (0-10), percentage, strengths, missing concepts, improvement suggestions,
        and overall diagnostic assessment.
        """
        transcript = (candidate_transcript or "").strip()

        # Handle empty/inaudible/inability answer
        inability_patterns = [
            "unable to answer", "cannot answer", "can't answer", "don't know", "do not know",
            "no idea", "not sure", "no answer", "have no clue", "not familiar", "pass",
            "skip", "next question", "sorry", "no experience", "blank", "idk"
        ]
        s_lower = transcript.lower().strip()
        s_clean = re.sub(r'[^\w\s]', ' ', s_lower).strip()
        words = s_clean.split()

        is_refusal = (
            not s_clean
            or (len(words) <= 3 and any(w in {"sorry", "no", "skip", "pass", "idk", "none", "na", "blank"} for w in words))
            or (len(words) < 25 and any(p in s_clean for p in inability_patterns))
        )

        if is_refusal:
            return {
                "score": 0.0,
                "maxScore": 10.0,
                "percentage": 0,
                "strengths": [],
                "missingConcepts": question_data.get("expected_concepts", [])[:4],
                "improvementSuggestions": [
                    "Study the core concepts: " + ", ".join(question_data.get("expected_concepts", [])[:3]),
                    "Provide a complete technical explanation covering architecture and trade-offs."
                ],
                "overallAssessment": f"Candidate indicated inability to answer ('{transcript}'). No marks awarded.",
                "evaluationMode": "Inability/Non-Answer Filter (0/10)"
            }

        # Check for external LLM evaluation if API key is provided
        if self.gemini_api_key or self.openai_api_key:
            try:
                llm_eval = self._evaluate_with_llm(question_data, transcript, role, experience_level)
                if llm_eval:
                    return llm_eval
            except Exception as e:
                logger.warning(f"LLM API evaluation failed, falling back to local rubric engine: {e}")

        # Local intelligent rubric-based evaluation
        return self._evaluate_with_local_rubric(question_data, transcript, role, experience_level)

    def _evaluate_with_local_rubric(
        self,
        question_data: Dict[str, Any],
        transcript: str,
        role: str,
        experience_level: str
    ) -> Dict[str, Any]:
        """
        Analyzes concept coverage, semantic length, reasoning structures, and technical terminology.
        """
        words = transcript.lower().split()
        word_count = len(words)
        lower_transcript = transcript.lower()

        expected_concepts: List[str] = question_data.get("expected_concepts", [])
        matched_concepts = []
        missed_concepts = []

        for concept in expected_concepts:
            pattern = r'\b' + re.escape(concept.lower()) + r'\b'
            if re.search(pattern, lower_transcript) or concept.lower() in lower_transcript:
                matched_concepts.append(concept)
            else:
                missed_concepts.append(concept)

        concept_ratio = len(matched_concepts) / max(1, len(expected_concepts))

        # Reasoning & structure cues
        reasoning_cues = ["because", "therefore", "for example", "such as", "in contrast", "difference is", "means that", "benefit", "tradeoff", "handles"]
        matched_cues = [cue for cue in reasoning_cues if cue in lower_transcript]
        has_examples = any(ex in lower_transcript for ex in ["for example", "e.g.", "for instance", "such as", "in my project", "scenario", "use case"])

        # Base technical score calculation (0 - 10 scale)
        concept_score = concept_ratio * 5.0

        if word_count < 15:
            length_score = 1.0
        elif word_count < 30:
            length_score = 1.8
        elif word_count < 75:
            length_score = 2.5
        elif word_count <= 220:
            length_score = 3.0
        else:
            length_score = 2.7

        reasoning_score = min(2.0, len(matched_cues) * 0.6 + (0.8 if has_examples else 0.0))

        if len(matched_concepts) == 0 and word_count < 12:
            raw_score = 0.0
        else:
            raw_score = concept_score + (length_score * 0.7 if matched_concepts else 0.0) + reasoning_score
        final_score = round(min(10.0, max(0.0, raw_score)), 1)
        percentage = int(round((final_score / 10.0) * 100))

        # Contextual strengths
        strengths = []
        if len(matched_concepts) >= 3:
            top_concepts = ", ".join(f"'{c}'" for c in matched_concepts[:3])
            strengths.append(f"Demonstrated solid grasp of key terminology ({top_concepts}).")
        elif matched_concepts:
            strengths.append(f"Correctly referenced relevant domain concept: '{matched_concepts[0]}'.")

        if has_examples:
            strengths.append("Provided concrete practical examples to illustrate the technical explanation.")
        if len(matched_cues) >= 2:
            strengths.append("Structured the answer with clear cause-and-effect reasoning.")
        if word_count >= 40:
            strengths.append("Delivered an articulate, well-developed explanation.")

        if not strengths:
            strengths.append("Directly attempted the question with foundational awareness.")

        # Improvement suggestions
        improvements = []
        if missed_concepts:
            missed_sample = ", ".join(f"'{c}'" for c in missed_concepts[:3])
            improvements.append(f"Incorporate discussion on {missed_sample} to provide a more comprehensive answer.")

        if not has_examples:
            improvements.append("Include a specific practical use case or code pattern to substantiate technical claims.")

        if word_count < 30:
            improvements.append("Expand on the underlying mechanics and trade-offs rather than providing a brief definition.")

        if not improvements:
            improvements.append("Maintain this high level of architectural clarity and depth in subsequent questions.")

        # Diagnostic summary
        if final_score >= 8.5:
            assessment = "Outstanding response demonstrating thorough conceptual mastery and structured technical articulation."
        elif final_score >= 7.0:
            assessment = "Strong answer showing good technical comprehension with minor room for additional architectural depth."
        elif final_score >= 5.0:
            assessment = "Satisfactory answer covering foundational aspects, but missing key technical nuances or practical examples."
        else:
            assessment = "Basic response requiring deeper conceptual precision and more detailed technical explanations."

        return {
            "score": final_score,
            "maxScore": 10.0,
            "percentage": percentage,
            "strengths": strengths,
            "missingConcepts": missed_concepts[:4],
            "improvementSuggestions": improvements,
            "overallAssessment": assessment,
            "evaluationMode": "Rubric Assessment Engine (Local)"
        }

    def _evaluate_with_llm(
        self,
        question_data: Dict[str, Any],
        transcript: str,
        role: str,
        experience_level: str
    ) -> Optional[Dict[str, Any]]:
        """Evaluates answer using Gemini or OpenAI."""
        prompt = (
            f"You are evaluating a candidate's answer in a technical interview.\n"
            f"Role: {role} (Level: {experience_level})\n"
            f"Question: {question_data.get('question')}\n"
            f"Expected Concepts: {', '.join(question_data.get('expected_concepts', []))}\n"
            f"Rubric: {json.dumps(question_data.get('rubric', {}))}\n"
            f"Candidate Answer: \"{transcript}\"\n\n"
            f"Evaluate this response objectively. Return ONLY a valid JSON object with these exact keys:\n"
            f"{{\n"
            f"  \"score\": 8.0,\n"
            f"  \"maxScore\": 10.0,\n"
            f"  \"percentage\": 80,\n"
            f"  \"strengths\": [\"Strength 1\", \"Strength 2\"],\n"
            f"  \"missingConcepts\": [\"Concept A\", \"Concept B\"],\n"
            f"  \"improvementSuggestions\": [\"Suggestion 1\", \"Suggestion 2\"],\n"
            f"  \"overallAssessment\": \"Concise 2-sentence diagnostic assessment.\",\n"
            f"  \"evaluationMode\": \"AI Neural Assessment (Gemini)\"\n"
            f"}}\n"
            f"Do not include any text, backticks, or markdown outside the JSON."
        )

        if self.gemini_api_key and self.gemini_api_key not in ("YOUR_API_KEY_HERE", "DUMMY_KEY"):
            for model_name in ["gemini-1.5-flash", "gemini-2.0-flash"]:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.gemini_api_key}"
                    headers = {"Content-Type": "application/json"}
                    payload = json.dumps({
                        "contents": [{"parts": [{"text": prompt}]}]
                    }).encode("utf-8")

                    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=8) as response:
                        res_json = json.loads(response.read().decode("utf-8"))
                        text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                        cleaned = self._clean_json_text(text)
                        parsed = json.loads(cleaned)
                        if "score" in parsed:
                            parsed["evaluationMode"] = "AI Neural Assessment (Gemini)"
                            parsed["maxScore"] = 10.0
                            parsed["score"] = round(min(10.0, max(0.0, float(parsed["score"]))), 1)
                            parsed["percentage"] = int(round((parsed["score"] / 10.0) * 100))
                            return parsed
                except Exception as ex:
                    logger.debug(f"Gemini evaluation error on {model_name}: {ex}")
                    continue

        if self.openai_api_key and self.openai_api_key.startswith("sk-"):
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_api_key}"
                }
                payload = json.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                }).encode("utf-8")

                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=8) as response:
                    res_json = json.loads(response.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    cleaned = self._clean_json_text(text)
                    parsed = json.loads(cleaned)
                    if "score" in parsed:
                        parsed["evaluationMode"] = "AI Neural Assessment (OpenAI)"
                        parsed["maxScore"] = 10.0
                        parsed["score"] = round(min(10.0, max(0.0, float(parsed["score"]))), 1)
                        parsed["percentage"] = int(round((parsed["score"] / 10.0) * 100))
                        return parsed
            except Exception as ex:
                logger.debug(f"OpenAI evaluation error: {ex}")

        return None

    def generate_recommendations(
        self,
        overall_score: float,
        answers: List[Dict[str, Any]],
        role: str,
        experience_level: str
    ) -> List[str]:
        """
        Generates structured, professional recommendations for candidate career development.
        """
        recs = []
        if overall_score >= 8.5:
            recs.append(f"Excellent technical mastery for {role}. Continue honing complex distributed systems architecture and system design.")
            recs.append("Consider taking on engineering mentorship responsibilities and technical specification design.")
        elif overall_score >= 7.0:
            recs.append(f"Solid competence in {role}. Deepen knowledge in scalability trade-offs, edge-case failure modes, and automated testing.")
            recs.append("Structure answers systematically using the STAR framework with concrete code examples.")
        elif overall_score >= 5.0:
            recs.append(f"Demonstrated foundational understanding of {role}. Review core documentation and strengthen theoretical underpinnings.")
            recs.append("Practice explaining technical mechanics out loud, emphasizing cause-and-effect relationships rather than brief definitions.")
        else:
            recs.append(f"Focus on strengthening fundamental computer science and {role} concepts through guided projects.")
            recs.append("Build end-to-end practical applications to substantiate theoretical knowledge with hands-on debugging experience.")

        # Aggregate common missed concepts
        all_missed = []
        for a in answers:
            eval_data = a.get("evaluation", {})
            missed = eval_data.get("missingConcepts", [])
            all_missed.extend(missed)

        if all_missed:
            unique_missed = list(dict.fromkeys(all_missed))[:4]
            sample = ", ".join(f"'{m}'" for m in unique_missed)
            recs.append(f"Key concepts to revisit for upcoming rounds: {sample}.")

        return recs

    @staticmethod
    def _clean_json_text(raw_text: str) -> str:
        """Removes markdown code fences and extraneous text around JSON."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        start_brace = cleaned.find("{")
        start_bracket = cleaned.find("[")
        if start_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
            end_bracket = cleaned.rfind("]")
            if end_bracket != -1:
                return cleaned[start_bracket : end_bracket + 1]
        elif start_brace != -1:
            end_brace = cleaned.rfind("}")
            if end_brace != -1:
                return cleaned[start_brace : end_brace + 1]
        return cleaned


interview_service = InterviewEvaluationEngine()
