# linalg

`Vector<T>` and `Matrix<T>` own dense data. Matrices use row-major storage.
Construct them with `zeros`, `fromRows` or `fromFlat`. The package provides dot
products, matrix multiplication, transposition, norms, outer and Kronecker
products. Norms and normalization use floating-point elements.

`LU<T>` uses partial pivoting for floating-point square matrices. Use `lu` to
reuse one factorization across several right-hand sides. A zero pivot reports
`Singular`, and a non-finite input or intermediate reports `NonFinite`.
`determinant` returns zero for a singular matrix. `solve` and `inverse` return
`Singular`.

Thin Householder QR supports matrices with at least as many rows as columns.
Use `solveLeastSquares` for full-column-rank systems. It checks diagonal
magnitudes relative to the largest diagonal, scaled by the row count and
the element type’s machine epsilon.

Tensor and strided view types, SVD and eigenvalue methods remain pending.
