interface Window {
    acquireVsCodeApi: <Message = unknown, State = unknown>() => {
        getState: () => State;
        setState: (data: State) => void;
        postMessage: (msg: Message) => void;
    };
}
