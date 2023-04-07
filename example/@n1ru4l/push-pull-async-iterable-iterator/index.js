"use strict";

/**
 * Attaches a cleanup handler to a AsyncIterable.
 *
 * @param source The source that should have a return handler attached
 * @param onReturn The return handler that should be attached
 * @returns
 */
function withHandlers(source, onReturn, onThrow) {
    const stream = (async function* withReturnSource() {
        yield* source;
    })();
    const originalReturn = stream.return.bind(stream);
    if (onReturn) {
        stream.return = (...args) => {
            onReturn();
            return originalReturn(...args);
        };
    }
    if (onThrow) {
        const originalThrow = stream.throw.bind(stream);
        stream.throw = (err) => {
            onThrow(err);
            return originalThrow(err);
        };
    }
    return stream;
}

function createDeferred() {
    const d = {};
    d.promise = new Promise((resolve, reject) => {
        d.resolve = resolve;
        d.reject = reject;
    });
    return d;
}
/**
 * makePushPullAsyncIterableIterator
 *
 * The iterable will publish values until return or throw is called.
 * Afterwards it is in the completed state and cannot be used for publishing any further values.
 * It will handle back-pressure and keep pushed values until they are consumed by a source.
 */
function makePushPullAsyncIterableIterator() {
    let state = {
        type: "running" /* running */,
    };
    let next = createDeferred();
    const values = [];
    function pushValue(value) {
        if (state.type !== "running" /* running */) {
            return;
        }
        values.push(value);
        next.resolve();
        next = createDeferred();
    }
    const source = (async function* PushPullAsyncIterableIterator() {
        while (true) {
            if (values.length > 0) {
                // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
                yield values.shift();
            } else {
                if (state.type === "error" /* error */) {
                    throw state.error;
                }
                if (state.type === "finished" /* finished */) {
                    return;
                }
                await next.promise;
            }
        }
    })();
    const stream = withHandlers(
        source,
        () => {
            if (state.type !== "running" /* running */) {
                return;
            }
            state = {
                type: "finished" /* finished */,
            };
            next.resolve();
        },
        (error) => {
            if (state.type !== "running" /* running */) {
                return;
            }
            state = {
                type: "error" /* error */,
                error,
            };
            next.resolve();
        },
    );
    return {
        pushValue,
        asyncIterableIterator: stream,
    };
}

const makeAsyncIterableIteratorFromSink = (make) => {
    const { pushValue, asyncIterableIterator } = makePushPullAsyncIterableIterator();
    const dispose = make({
        next: (value) => {
            pushValue(value);
        },
        complete: () => {
            // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
            asyncIterableIterator.return();
        },
        error: (err) => {
            // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
            asyncIterableIterator.throw(err);
        },
    });
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    const originalReturn = asyncIterableIterator.return;
    let returnValue = undefined;
    asyncIterableIterator.return = () => {
        if (returnValue === undefined) {
            dispose();
            returnValue = originalReturn();
        }
        return returnValue;
    };
    return asyncIterableIterator;
};

function applyAsyncIterableIteratorToSink(asyncIterableIterator, sink) {
    const run = async () => {
        try {
            for await (const value of asyncIterableIterator) {
                sink.next(value);
            }
            sink.complete();
        } catch (err) {
            sink.error(err);
        }
    };
    run();
    return () => {
        var _a;
        (_a = asyncIterableIterator.return) === null || _a === void 0
            ? void 0
            : _a.call(asyncIterableIterator);
    };
}

function isAsyncIterable(input) {
    return (
        typeof input === "object" &&
        input !== null &&
        // The AsyncGenerator check is for Safari on iOS which currently does not have
        // Symbol.asyncIterator implemented
        // That means every custom AsyncIterable must be built using a AsyncGeneratorFunction (async function * () {})
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (input[Symbol.toStringTag] === "AsyncGenerator" ||
            (Symbol.asyncIterator && Symbol.asyncIterator in input))
    );
}

/**
 * Attaches a cleanup handler from and AsyncIterable to an AsyncIterable.
 *
 * @param source
 * @param target
 */
function withHandlersFrom(
    /** The source that should be returned with attached handlers. */
    source,
    /**The target on which the return and throw methods should be called. */
    target,
) {
    return withHandlers(
        source,
        () => {
            var _a;
            return (_a = target.return) === null || _a === void 0
                ? void 0
                : _a.call(target);
        },
        (err) => {
            var _a;
            return (_a = target.throw) === null || _a === void 0
                ? void 0
                : _a.call(target, err);
        },
    );
}

function filter(filter) {
    return async function* filterGenerator(asyncIterable) {
        for await (const value of asyncIterable) {
            if (filter(value)) {
                yield value;
            }
        }
    };
}

/**
 * Map the events published by an AsyncIterable.
 */
const map = (map) =>
    async function* mapGenerator(asyncIterable) {
        for await (const value of asyncIterable) {
            yield map(value);
        }
    };
