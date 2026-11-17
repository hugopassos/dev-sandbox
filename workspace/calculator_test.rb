require 'minitest/autorun'
require_relative 'calculator'

class CalculatorTest < Minitest::Test
  def setup
    @calculator = Calculator.new
  end

  def test_add_positive_numbers
    assert_equal 5, @calculator.add(2, 3)
  end

  def test_add_negative_numbers
    assert_equal(-5, @calculator.add(-2, -3))
  end

  def test_add_zero
    assert_equal 7, @calculator.add(7, 0)
  end

  def test_subtract_positive_numbers
    assert_equal 2, @calculator.subtract(5, 3)
  end

  def test_subtract_produces_negative_result
    assert_equal(-2, @calculator.subtract(3, 5))
  end

  def test_subtract_negative_number
    assert_equal 8, @calculator.subtract(5, -3)
  end

  def test_subtract_zero
    assert_equal 7, @calculator.subtract(7, 0)
  end

  def test_decimal_numbers
    assert_in_delta 0.3, @calculator.add(0.1, 0.2), 0.000001
    assert_in_delta 0.1, @calculator.subtract(0.3, 0.2), 0.000001
  end
end
