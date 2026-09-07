from core.auth import hash_password, normalize_student_id, verify_password


def test_password_is_salted_and_verifiable():
    password = "A-secure-test-password"
    first = hash_password(password)
    second = hash_password(password)

    assert first != password
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("wrong-password", first)


def test_student_id_is_normalized():
    assert normalize_student_id("  stu-001  ") == "STU-001"
