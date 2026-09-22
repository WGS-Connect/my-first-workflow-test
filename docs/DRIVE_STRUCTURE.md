# Drive persistence

The existing Drive checkpoint/commit protocol remains in place. Queue state is stored per book under the audiobook channel namespace; individual jobs retain resumable stage state and upload state.
