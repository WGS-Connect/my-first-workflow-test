# Architecture

The pipeline has these checkpointed stages:

`source → research → script → audio → visuals → render → qa → package → upload → thumbnail → verify → cleanup`

The visual stage only selects reusable local assets. The render stage creates a fixed composition over moving premade backgrounds. The YouTube client is unchanged.
